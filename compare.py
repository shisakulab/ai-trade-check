"""複数のリプレイ結果（SQLite）を1つの表で比べる。

  python compare.py replay_a.db replay_b.db
  python compare.py replay_ai_a.db+replay_ai_b.db replay_dummy2025_u30.db
      「+」でつないだものは同じ設定の複数回分としてまとめ、平均とばらつきも出す
"""
import argparse
import json
import math
import os
import sqlite3
import statistics

import server

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRADING_DAYS = 245


def stats(db_path, initial):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT date, equity FROM snapshots ORDER BY date").fetchall()
        trades = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'filled' AND IFNULL(note, '') != 'コアETF'").fetchone()[0]
    finally:
        conn.close()
    if len(rows) < 2:
        raise SystemExit(f"{db_path}: 記録が2日分未満です")
    dates = [d for d, _ in rows]
    eq = [e for _, e in rows]
    return {"dates": dates, **metrics(eq, initial), "trades": trades}


def metrics(eq, initial):
    peak, dd = 0.0, 0.0
    for e in eq:
        peak = max(peak, e)
        dd = max(dd, 1 - e / peak)
    rets = [b / a - 1 for a, b in zip(eq, eq[1:])]
    sd = statistics.pstdev(rets)
    years = len(eq) / TRADING_DAYS
    return {
        "pnl": eq[-1] - initial,
        "ret": eq[-1] / initial - 1,
        "cagr": (eq[-1] / initial) ** (1 / years) - 1,
        "maxdd": dd,
        "sharpe": statistics.mean(rets) / sd * math.sqrt(TRADING_DAYS) if sd else 0.0,
    }


def fmt_row(label, m, trades="—"):
    return (f"{label:<34} {m['pnl']:>+12,.0f} {m['ret']:>+8.1%} {m['cagr']:>+8.1%} "
            f"{m['maxdd']:>7.1%} {m['sharpe']:>6.2f} {trades:>6}")


def main():
    parser = argparse.ArgumentParser(description="リプレイ結果を比べる")
    parser.add_argument("dbs", nargs="+", help="リプレイの DB。同じ設定の複数回分は + でつなぐ")
    args = parser.parse_args()
    path = os.path.join(BASE_DIR, "config.json")
    if not os.path.exists(path):
        path = os.path.join(BASE_DIR, "config.example.json")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    initial = cfg["initial_cash"]

    print(f"{'':<34} {'損益(円)':>11} {'リターン':>6} {'年率':>6} {'最大DD':>5} {'シャープ':>4} {'売買数':>4}")
    periods = set()
    first_dates = None
    for group in args.dbs:
        paths = group.split("+")
        runs = [stats(p, initial) for p in paths]
        for p, r in zip(paths, runs):
            periods.add((r["dates"][0], r["dates"][-1]))
            print(fmt_row(os.path.basename(p), r, r["trades"]))
        first_dates = first_dates or runs[0]["dates"]
        if len(runs) > 1:
            avg = {k: statistics.mean(r[k] for r in runs) for k in ("pnl", "ret", "cagr", "maxdd", "sharpe")}
            sd = statistics.stdev(r["ret"] for r in runs)
            print(fmt_row(f"  └ 平均（{len(runs)}回, リターンのばらつき ±{sd:.1%}）", avg))

    if len(periods) > 1:
        print(f"\n⚠ 期間がそろっていません: {sorted(periods)}")
    print()
    for b in server.benchmarks(cfg, first_dates):
        eq = [v for v in b["equity"] if v is not None]
        print(fmt_row(b["name"] + ("（円換算）" if b["yen"] else ""), metrics(eq, initial)))
    print(f"\n期間: {first_dates[0]} 〜 {first_dates[-1]}（指数は配当を含まない）")


if __name__ == "__main__":
    main()
