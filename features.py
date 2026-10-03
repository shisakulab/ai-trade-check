"""日足からテクニカル指標を計算し、AI に渡す state テキストを作る。"""

# 52週高値・120日騰落に使う日数
LONG_DAYS = 250
MIN_DAYS = 121


def rsi(close, n=14):
    diff = close.diff()
    gain = diff.clip(lower=0).rolling(n).mean()
    loss = (-diff.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + gain / loss)


def compute(df):
    """最新日の指標を dict で返す。データ不足なら None。"""
    if len(df) < MIN_DAYS:
        return None
    c, v = df["Close"], df["Volume"]
    sma5, sma25 = c.rolling(5).mean(), c.rolling(25).mean()
    sma75 = c.rolling(75).mean()
    return {
        "close": float(c.iloc[-1]),
        "chg_1d": float(c.iloc[-1] / c.iloc[-2] - 1),
        "chg_5d": float(c.iloc[-1] / c.iloc[-6] - 1),
        "chg_20d": float(c.iloc[-1] / c.iloc[-21] - 1),
        "chg_60d": float(c.iloc[-1] / c.iloc[-61] - 1),
        "chg_120d": float(c.iloc[-1] / c.iloc[-121] - 1),
        "sma5": float(sma5.iloc[-1]),
        "sma25": float(sma25.iloc[-1]),
        "sma75": float(sma75.iloc[-1]),
        "sma5_prev": float(sma5.iloc[-2]),
        "sma25_prev": float(sma25.iloc[-2]),
        "rsi14": float(rsi(c).iloc[-1]),
        "vol_ratio": float(v.iloc[-1] / v.iloc[-21:-1].mean()),
        "high_20d": float(df["High"].iloc[-20:].max()),
        "low_20d": float(df["Low"].iloc[-20:].min()),
        "high_52w": float(df["High"].iloc[-LONG_DAYS:].max()),
        "low_52w": float(df["Low"].iloc[-LONG_DAYS:].min()),
    }


def market(df):
    """市場全体（日経平均）の騰落。データ不足なら None。"""
    if df is None or len(df) < 76:
        return None
    c = df["Close"]
    return {
        "chg_5d": float(c.iloc[-1] / c.iloc[-6] - 1),
        "chg_20d": float(c.iloc[-1] / c.iloc[-21] - 1),
        "chg_60d": float(c.iloc[-1] / c.iloc[-61] - 1),
        "above_sma75": bool(c.iloc[-1] > c.rolling(75).mean().iloc[-1]),
    }


def state_text(symbol, name, date, f, position, mkt=None, held_days=None, stop_loss_pct=None, entry=None, disclosures=None):
    lines = [
        f"Japanese stock {symbol} ({name}), daily data as of {date} close.",
        f"Close: {f['close']:.1f} JPY. Change 1d {f['chg_1d']:+.2%}, 5d {f['chg_5d']:+.2%}, 20d {f['chg_20d']:+.2%}, "
        f"60d {f['chg_60d']:+.2%}, 120d {f['chg_120d']:+.2%}.",
        f"SMA5 {f['sma5']:.1f} (prev {f['sma5_prev']:.1f}), SMA25 {f['sma25']:.1f} (prev {f['sma25_prev']:.1f}), SMA75 {f['sma75']:.1f}.",
        f"RSI14 {f['rsi14']:.1f}. Volume vs 20d average: {f['vol_ratio']:.2f}x.",
        f"20-day range: {f['low_20d']:.1f} - {f['high_20d']:.1f}. "
        f"52-week range: {f['low_52w']:.1f} - {f['high_52w']:.1f} ({f['close'] / f['high_52w'] - 1:+.1%} from 52-week high).",
    ]
    if mkt:
        lines.append(
            f"Market (Nikkei 225): 5d {mkt['chg_5d']:+.2%}, 20d {mkt['chg_20d']:+.2%}, 60d {mkt['chg_60d']:+.2%}, "
            f"{'above' if mkt['above_sma75'] else 'below'} its 75-day average."
        )
    if position:
        pnl = f["close"] / position["avg_cost"] - 1
        held = f", held {held_days} trading days" if held_days is not None else ""
        lines.append(
            f"Current position: long {position['qty']} shares, avg cost {position['avg_cost']:.1f}, unrealized {pnl:+.2%}{held}."
        )
    else:
        lines.append("Current position: none (long-only account, no short selling).")
    if disclosures:
        lines.append("Recent company disclosures (TDnet, Japanese titles): " + " / ".join(disclosures))
    if stop_loss_pct:
        lines.append(f"A stop-loss automatically exits any position at {-stop_loss_pct:.0%} unrealized loss.")
    if entry and entry.get("buy") == "dip":
        lines.append(
            f"A buy would be placed as a limit order {entry['dip_pct']:.1%} below this close, valid for {entry['valid_days']} "
            "trading days (no fill if the price does not dip that far). A sell executes at the next trading day's open. "
            "Holding period is days to weeks."
        )
    else:
        lines.append("The order would execute at the next trading day's open. Holding period is days to weeks.")
    return "\n".join(lines)
