"""仮想約定。
成行：注文を出した日の翌営業日の始値（＋スリッページ・手数料）で約定させる。
指値（買い）：有効期間内に安値が指値を下回った最初の日に、指値（寄付きが指値以下なら始値）で約定させる。
期間内に下回らなければ期限切れにする。"""
import store


def settle(conn, bars, costs, initial_cash, as_of):
    """未約定の注文を、as_of までに来た翌営業日の始値で約定させる。約定件数を返す。"""
    filled = 0
    # 売りを先に約定させ、その代金で同じ日の買いを約定できるようにする
    for o in sorted(store.pending_orders(conn), key=lambda o: o["side"] != "sell"):
        df = bars.get(o["symbol"])
        if df is None:
            continue
        nxt = [d for d in df.index if o["decided_on"] < d <= as_of]
        if not nxt:
            continue
        if o["limit_price"] is not None:
            window = nxt[: o["valid_days"]]
            hit = next((d for d in window if float(df.loc[d, "Low"]) < o["limit_price"]), None)
            if hit is None:
                if len(window) >= o["valid_days"]:
                    store.reject_order(conn, o["id"], "指値まで下がらず期限切れ", status="expired")
                continue
            day = hit
            price = min(float(df.loc[day, "Open"]), o["limit_price"])
        else:
            day = nxt[0]
            open_ = float(df.loc[day, "Open"])
            slip = costs["slippage_rate"]
            price = open_ * (1 + slip) if o["side"] == "buy" else open_ * (1 - slip)
        fee = o["qty"] * price * costs["commission_rate"]
        cash, positions = store.portfolio(conn, initial_cash, costs.get("tax_rate", 0.0))
        if o["side"] == "buy" and o["qty"] * price + fee > cash:
            store.reject_order(conn, o["id"], "約定時に現金不足")
            continue
        if o["side"] == "sell" and positions.get(o["symbol"], {}).get("qty", 0) < o["qty"]:
            store.reject_order(conn, o["id"], "約定時に保有株数不足")
            continue
        store.fill_order(conn, o["id"], day, price, fee)
        filled += 1
    return filled
