"""1日1回（大引け後）に実行する：データ取得 → 仮想約定 → AI判断 → 安全装置 → 注文記録。

  python main.py                                   通常実行（最新の営業日で判断）
  python main.py --replay 2026-07-01 2026-09-25 --db replay.db   過去の期間を1日ずつ再生
  python main.py --entry open                      買いの入り方を config の entry より優先（open / dip）
  python main.py --strategy momentum               モメンタムで選び AI は拒否権だけ（config の strategy より優先）
  python main.py --no-core                         待機資金をコアETFに置かない
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import broker.paper as paper
import data
import features
import ai_engine
import risk
import store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOP_FILE = os.path.join(BASE_DIR, "STOP")
ACTION_JA = {"buy": "買い", "sell": "売り", "hold": "様子見"}


def load_config():
    """config.json があればそれ、なければ config.example.json を読む。"""
    path = os.path.join(BASE_DIR, "config.json")
    if not os.path.exists(path):
        path = os.path.join(BASE_DIR, "config.example.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def screen(cfg, feats, positions):
    """AI に聞く銘柄を絞る。保有中の銘柄は必ず聞き、残りは上昇トレンド（終値が75日線より上）で
    100株を上限額内で買えるものを60日騰落率の高い順に top_n 件。screen 設定がなければ全銘柄。"""
    sc = cfg.get("screen")
    if not sc:
        return list(feats)
    held = [s for s in feats if s in positions]
    cap = cfg["risk"]["max_position_yen"]
    pool = [s for s, f in feats.items() if s not in positions and f["close"] > f["sma75"] and f["close"] * 100 <= cap]
    pool.sort(key=lambda s: -feats[s]["chg_60d"])
    return held + pool[: sc["top_n"]]


def momentum_keep(cfg, feats):
    """モメンタム戦略で持ち続けてよい銘柄（上位 top_n の2倍まで）と、買う銘柄（上位 top_n）。"""
    cap = cfg["risk"]["max_position_yen"]
    n = cfg["screen"]["top_n"]
    ranked = [s for s, f in feats.items() if f["close"] > f["sma75"] and f["close"] * 100 <= cap]
    ranked.sort(key=lambda s: -feats[s]["chg_60d"])
    return set(ranked[: n * 2]), ranked[:n]


def is_rebalance_day(date, prev_date):
    """週の最初の営業日か。"""
    if prev_date is None:
        return True
    week = lambda d: datetime.strptime(d, "%Y-%m-%d").isocalendar()[:2]
    return week(date) != week(prev_date)


def run_day(conn, cfg, bars_full, market_full, date, engine, entry, strategy="ai", use_core=True, live=False, prev_date=None):
    bars = {s: df[df.index <= date] for s, df in bars_full.items()}
    mkt = features.market(market_full[market_full.index <= date] if market_full is not None else None)
    initial = cfg["initial_cash"]
    r = cfg["risk"]

    paper.settle(conn, bars, cfg["costs"], initial, date)
    cash, positions = store.portfolio(conn, initial, cfg["costs"].get("tax_rate", 0.0))
    closes = {s: float(df["Close"].iloc[-1]) for s, df in bars.items() if len(df)}
    value = sum(p["qty"] * closes[s] for s, p in positions.items())
    equity = cash + value
    core = cfg.get("core") if use_core else None
    core_sym = core["symbol"] if core else None
    core_close = closes.get(core_sym) if core else None
    core_qty = positions.get(core_sym, {}).get("qty", 0) if core else 0
    core_value = core_qty * core_close if core_qty else 0.0
    store.save_snapshot(conn, date, cash, value)
    conn.commit()

    print(f"=== {date}  資産 {equity:,.0f}円（現金 {cash:,.0f} / 株 {value:,.0f}）  損益 {equity - initial:+,.0f}円 ===")
    if store.has_decisions(conn, date):
        print("  この日の判断は記録済みのためスキップします")
        return

    stopped = os.path.exists(STOP_FILE)
    block = risk.global_block(r, equity, store.previous_equity(conn, date), initial, stopped)
    if block:
        print(f"  ⚠ {block}")
    # 未約定の買い指値の分は先に確保しておく。コアETFは売って個別株の資金にできる
    reserved = store.pending_buys(conn)
    slip = cfg["costs"]["slippage_rate"]
    cash_free = cash - sum(reserved.values())
    cash_available = cash_free + core_value * (1 - slip)
    exposure_left = max(0, risk.exposure_room(r, equity, value - core_value) - sum(reserved.values()))
    dip = entry.get("buy") == "dip"
    orders_today = store.orders_decided_on(conn, date)

    feats = {}
    for symbol in cfg["watchlist"]:
        df = bars.get(symbol)
        if df is not None and len(df) and df.index[-1] == date:
            f = features.compute(df)
            if f is not None:
                feats[symbol] = f
    momentum = strategy == "momentum"
    keep = None
    if momentum:
        rebalance = is_rebalance_day(date, prev_date)
        keep, top = momentum_keep(cfg, feats) if rebalance else (None, [])
        held = [s for s in feats if s in positions]
        targets = held + [s for s in top if s not in positions]
        names = ", ".join(cfg["watchlist"][s] for s in top)
        print(f"  候補 {len(feats)} 銘柄 ・ " + (f"入れ替え日：上位 {names}" if rebalance else "入れ替えなし（損切りだけ確認）"))
    else:
        targets = screen(cfg, feats, positions)
        print(f"  候補 {len(feats)} 銘柄から {len(targets)} 銘柄を AI に聞きます")

    for symbol in targets:
        name, df, f = cfg["watchlist"][symbol], bars[symbol], feats[symbol]
        pos = positions.get(symbol)
        held_days = int((df.index >= pos["since"]).sum()) if pos else None
        stop = risk.stop_loss(r, f["close"], pos["avg_cost"]) if pos else None
        asks_ai = engine == "ai" and not stop and not (momentum and pos)
        disclosures = data.fetch_disclosures(symbol, date) if live and asks_ai else None
        state = features.state_text(symbol, name, date, f, pos, mkt, held_days, r["stop_loss_pct"], entry, disclosures)
        if stop:
            # 損切りは AI に聞かず、停止条件・注文件数上限にもかけずに必ず売る
            action, conf, raw, decided_by = "sell", 1.0, stop, "rule"
        elif momentum and pos:
            if keep is not None and symbol not in keep:
                action, conf, raw = "sell", 1.0, "モメンタム上位圏外または75日線割れ"
            else:
                action, conf, raw = "hold", 1.0, "保有継続"
            decided_by = "rule"
        elif momentum:
            avoid, vconf, raw = ai_engine.veto(state) if engine == "ai" else (False, 0.0, "rule")
            action, conf = ("hold", vconf) if avoid else ("buy", 1.0)
            decided_by = "momentum+ai" if engine == "ai" else "momentum"
        else:
            action, conf, raw = ai_engine.decide(engine, state, f)
            decided_by = engine

        held = pos["qty"] if pos else 0
        limit = round(f["close"] * (1 - entry["dip_pct"]), 1) if dip and action == "buy" else None
        if action == "buy":
            qty = risk.buy_qty(r, limit or f["close"], held, min(cash_available, exposure_left))
        elif action == "sell":
            qty = held
        else:
            qty = 0
        if stop:
            reason = None
        elif block and action != "hold" and (action == "buy" or stopped):
            # 損失上限で止まったときは買いだけ止め、保有を減らす売りは通す。STOP ファイルは売りも止める
            reason = block
        elif action == "buy" and symbol in reserved:
            reason = "未約定の買い指値あり"
        else:
            reason = risk.check_order(r, action, conf, orders_today, qty)

        if reason is None:
            if action == "sell":
                store.cancel_pending_buys(conn, symbol, "売り判断のため取消")
            store.add_order(conn, date, symbol, action, qty, limit_price=limit, valid_days=entry["valid_days"] if limit else None)
            orders_today += 1
            if action == "buy":
                cost = qty * limit if limit else qty * f["close"] * (1 + slip)
                cash_available -= cost
                cash_free -= cost
                exposure_left -= cost
            if limit:
                outcome = f"買い {qty}株 を指値 {limit:,.1f}円（{entry['valid_days']}営業日有効）で発注"
            else:
                outcome = f"{ACTION_JA[action]} {qty}株 を翌営業日の寄付きで発注" + (f"（{stop}）" if stop else "")
        else:
            outcome = "見送り" if reason == "hold" else f"見送り: {reason}"
            if momentum and action == "hold" and pos is None:
                outcome = "見送り: AI が拒否"
        store.add_decision(conn, date, symbol, f["close"], decided_by, action, conf, state, raw, outcome)
        if not (momentum and pos and action == "hold"):
            print(f"  {name:<8} {f['close']:>9,.1f}円  {ACTION_JA[action]}({conf:.2f})  → {outcome}")

    # 余った現金はコアETFに置き、足りなければコアを売る（STOP ファイルがあるときは動かさない）
    if core and core_close and not stopped and not store.has_pending(conn, core_sym):
        order = risk.core_order(cash_free, core_qty, core_close * (1 + slip), equity,
                                core.get("cash_buffer_ratio", 0.02), core.get("min_trade_ratio", 0.05))
        if order:
            side, qty = order
            store.add_order(conn, date, core_sym, side, qty, note="コアETF")
            print(f"  {core['name']:<8} {core_close:>9,.1f}円  コア{ACTION_JA[side]} {qty}口 を翌営業日の寄付きで発注")
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="AI 売買ペーパートレード検証")
    parser.add_argument("--db", default=os.path.join(BASE_DIR, "trades.db"), help="記録先 SQLite ファイル")
    parser.add_argument("--replay", nargs=2, metavar=("START", "END"), help="過去の期間を1日ずつ再生（YYYY-MM-DD）")
    parser.add_argument("--engine", choices=["dummy", "ai"], help="config の engine を上書き")
    parser.add_argument("--entry", choices=["open", "dip"], help="config の entry.buy を上書き（open: 翌日寄付きで成行 / dip: 押し目指値）")
    parser.add_argument("--strategy", choices=["ai", "momentum"], help="config の strategy を上書き（ai: AI が売買判断 / momentum: モメンタム＋AI の拒否権）")
    parser.add_argument("--no-core", action="store_true", help="待機資金をコアETFに置かない")
    parser.add_argument("--no-loss-limit", action="store_true", help="検証用：1日・累計の損失上限による停止を無効にする（--replay のときだけ有効）")
    args = parser.parse_args()

    cfg = load_config()
    if args.no_loss_limit:
        if not args.replay:
            sys.exit("--no-loss-limit は --replay と一緒にしか使えません")
        cfg["risk"]["daily_loss_limit_yen"] = cfg["risk"]["total_loss_limit_yen"] = float("inf")
    if cfg["mode"] != "paper":
        sys.exit("実弾モード（楽天 RSS 連携）はまだ実装していません。config.json の mode を paper にしてください。")
    engine = ai_engine.engine_name(args.engine or cfg["engine"])
    print(f"判断エンジン: {engine}" + ("（AI 未設定のため SMA クロスで判断）" if engine == "dummy" else ""))
    entry = {"buy": "open", "dip_pct": 0.01, "valid_days": 3, **cfg.get("entry", {})}
    if args.entry:
        entry["buy"] = args.entry
    if entry["buy"] == "dip":
        print(f"買いの入り方: 終値の {entry['dip_pct']:.1%} 下に指値（{entry['valid_days']}営業日有効）")

    strategy = args.strategy or cfg.get("strategy", "ai")
    use_core = bool(cfg.get("core")) and not args.no_core
    print(f"戦略: {'モメンタム＋AI の拒否権' if strategy == 'momentum' else 'AI が売買判断'} ・ コアETF: {cfg['core']['name'] if use_core else 'なし'}")

    index = cfg.get("market_index")
    # 指標（52週高値など）の計算に、開始日より前の1年余りが必要
    fetch_start = None
    if args.replay:
        fetch_start = (datetime.strptime(args.replay[0], "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
    extra = [index] if index else []
    if use_core:
        extra.append(cfg["core"]["symbol"])
    bars = data.fetch_daily(list(cfg["watchlist"]) + extra, period="2y", start=fetch_start)
    market = bars.pop(index, None) if index else None
    all_days = sorted({d for df in bars.values() for d in df.index})
    days = all_days
    if not days:
        sys.exit("株価データを取得できませんでした。ネット接続を確認してください。")
    if args.replay:
        start, end = args.replay
        days = [d for d in days if start <= d <= end]
    else:
        days = days[-1:]

    conn = store.connect(args.db)
    try:
        for d in days:
            i = all_days.index(d)
            prev = all_days[i - 1] if i else None
            run_day(conn, cfg, bars, market, d, engine, entry, strategy, use_core, live=not args.replay, prev_date=prev)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
