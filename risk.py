"""発注前の安全装置。止める理由があれば文字列を返し、問題なければ None を返す。"""
import math


def global_block(risk, equity, prev_equity, initial_cash, stop_file_exists):
    """その日の新規注文をすべて止めるべきか。"""
    if stop_file_exists:
        return "STOP ファイルがあるため停止中"
    if initial_cash - equity >= risk["total_loss_limit_yen"]:
        return f"累計損失が上限（{risk['total_loss_limit_yen']:,}円）に達したため停止"
    if prev_equity is not None and prev_equity - equity >= risk["daily_loss_limit_yen"]:
        return f"1日の損失が上限（{risk['daily_loss_limit_yen']:,}円）に達したため停止"
    return None


def stop_loss(risk, close, avg_cost):
    """含み損が stop_loss_pct 以上なら損切り理由を返す。AI の判断や停止条件より優先する。"""
    loss = 1 - close / avg_cost
    if round(loss, 9) >= risk["stop_loss_pct"]:
        return f"損切り（含み損 {-loss:+.1%}）"
    return None


def exposure_room(risk, equity, stock_value):
    """総保有上限（資産 × max_exposure_ratio）まであと何円分買えるか。"""
    return max(0, equity * risk["max_exposure_ratio"] - stock_value)


def buy_qty(risk, price, held_qty, cash_available):
    """1銘柄の上限額と現金（総保有上限で絞ったもの）の範囲で買える株数（100株単位）。"""
    room = min(risk["max_position_yen"] - held_qty * price, cash_available)
    return max(0, math.floor(room / (price * 100)) * 100)


def check_order(risk, action, confidence, orders_today, qty):
    if action == "hold":
        return "hold"
    if confidence < risk["min_confidence"]:
        return f"確信度 {confidence:.2f} が閾値 {risk['min_confidence']} 未満"
    if orders_today >= risk["max_orders_per_day"]:
        return f"1日の注文上限（{risk['max_orders_per_day']}件）に到達"
    if qty <= 0:
        return "保有していない" if action == "sell" else "上限額・総保有上限・現金の範囲で買える株数がない"
    return None


def core_order(cash_free, core_qty, core_price, equity, buffer_ratio, min_trade_ratio):
    """待機資金を置くコアETFの注文。(side, qty) か None を返す。
    cash_free は今日の個別株の注文と未約定の指値を差し引いた現金。
    現金が足りなければ手元に buffer 分が残るまでコアを売る。
    現金が buffer より min_trade 分以上多ければ buffer まで買い増す（細かい売買はしない）。"""
    buffer = equity * buffer_ratio
    if cash_free < 0:
        qty = min(core_qty, math.ceil((buffer - cash_free) / core_price))
        return ("sell", qty) if qty > 0 else None
    if cash_free - buffer >= equity * min_trade_ratio:
        qty = math.floor((cash_free - buffer) / core_price)
        return ("buy", qty) if qty > 0 else None
    return None
