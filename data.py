"""yfinance で日足を取得する（無料・遅延あり。ペーパートレード用）。"""
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import yfinance as yf

JST = timezone(timedelta(hours=9))
TDNET_API = "https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=10"
# 大引け(15:30)後、データが確定するまでの余裕を見た時刻
CLOSE_SETTLED = (16, 0)


def fetch_daily(symbols, period="1y", start=None):
    """{symbol: DataFrame(Open, High, Low, Close, Volume)} を返す。index は 'YYYY-MM-DD' の文字列。
    start（YYYY-MM-DD）を渡すと period の代わりにその日以降を取得する。"""
    span = {"start": start} if start else {"period": period}
    raw = yf.download(list(symbols), interval="1d", progress=False, auto_adjust=False, group_by="ticker", **span)
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    settled = (now.hour, now.minute) >= CLOSE_SETTLED
    out = {}
    for s in symbols:
        df = raw[s][["Open", "High", "Low", "Close", "Volume"]].dropna().copy()
        df.index = [d.strftime("%Y-%m-%d") for d in df.index]
        # 場中に実行したときの未確定の当日足は使わない
        if not settled and len(df) and df.index[-1] == today:
            df = df.iloc[:-1]
        out[s] = df
        jumps = df["Close"] / df["Close"].shift()
        bad = jumps[(jumps > 1.35) | (jumps < 0.65)]
        if len(bad):
            # 株式分割が価格に反映されていないなど、データが壊れている可能性がある
            print(f"⚠ {s} の価格が1日で大きく動いた日があります（データ異常の可能性）: "
                  + ", ".join(f"{d} {v:.2f}倍" for d, v in bad.items()))
    return out


def fetch_disclosures(symbol, date, days=7):
    """date までの直近 days 日の適時開示タイトル（新しい順）。非公式の TDnet API を使い、失敗したら空リスト。"""
    code = symbol.split(".")[0]
    try:
        with urllib.request.urlopen(TDNET_API.format(code=code), timeout=15) as res:
            items = json.loads(res.read().decode("utf-8"))["items"]
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        return []
    since = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    return [
        f"{i['Tdnet']['pubdate'][:10]} {i['Tdnet']['title']}"
        for i in items
        if since <= i["Tdnet"]["pubdate"][:10] <= date
    ]
