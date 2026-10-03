"""ペーパートレードの記録（trades.db）をこのパソコンの中だけで表示するダッシュボード。"""
import argparse
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import data
import store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_FILE = "dashboard.html"
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
STOP_FILE = os.path.join(BASE_DIR, "STOP")


_bench_cache = {}


def benchmarks(cfg, dates):
    """記録の初日に元本を各指数へ入れていたら、各日にいくらになっていたか（円換算）。
    その日までに確定した直近の終値を使う。取得に失敗したら空リストを返す。"""
    specs = cfg.get("benchmarks", [])
    if len(dates) < 2 or not specs:
        return []
    key = (dates[0], dates[-1], json.dumps(specs))
    if key in _bench_cache:
        return _bench_cache[key]
    symbols = sorted({b["symbol"] for b in specs} | {b["fx"] for b in specs if b.get("fx")})
    try:
        start = (datetime.strptime(dates[0], "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
        bars = data.fetch_daily(symbols, start=start)
    except Exception as e:  # yfinance はネットワーク系で様々な例外を出す
        print(f"ベンチマークを取得できませんでした: {e}")
        return []

    def asof(sym, d):
        c = bars[sym]["Close"]
        c = c[c.index <= d]
        return float(c.iloc[-1]) if len(c) else None

    out = []
    for b in specs:
        vals = []
        for d in dates:
            px = asof(b["symbol"], d)
            fx = asof(b["fx"], d) if b.get("fx") else 1.0
            vals.append(px * fx if px is not None and fx is not None else None)
        base = vals[0]
        if not base:
            continue
        out.append({
            "symbol": b["symbol"], "name": b["name"], "yen": bool(b.get("fx")),
            "equity": [None if v is None else cfg["initial_cash"] * v / base for v in vals],
        })
    _bench_cache[key] = out
    return out


def build_summary(db_path):
    path = CONFIG_FILE if os.path.exists(CONFIG_FILE) else os.path.join(BASE_DIR, "config.example.json")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not os.path.exists(db_path):
        return {"config": cfg, "snapshots": [], "benchmarks": [], "positions": [], "decisions": [], "orders": [], "stopped": os.path.exists(STOP_FILE)}
    if cfg.get("core"):
        cfg["watchlist"] = {**cfg["watchlist"], cfg["core"]["symbol"]: cfg["core"]["name"]}
    conn = store.connect(db_path)
    try:
        snapshots = [dict(r) for r in conn.execute("SELECT * FROM snapshots ORDER BY date")]
        tax_rate = cfg["costs"].get("tax_rate", 0.0)
        cash, positions = store.portfolio(conn, cfg["initial_cash"], tax_rate)
        realized = store.realized_by_year(conn)
        last_close = {
            r["symbol"]: r["close"]
            for r in conn.execute("SELECT symbol, close FROM decisions d WHERE date = (SELECT MAX(date) FROM decisions WHERE symbol = d.symbol)")
        }
        rows = lambda sql: [dict(r) for r in conn.execute(sql)]
        return {
            "config": cfg,
            "stopped": os.path.exists(STOP_FILE),
            "cash": cash,
            "tax_paid": sum(max(0.0, v) * tax_rate for v in realized.values()),
            "snapshots": snapshots,
            "benchmarks": benchmarks(cfg, [r["date"] for r in snapshots]),
            "positions": [
                {"symbol": s, "name": cfg["watchlist"].get(s, s), "qty": p["qty"], "avg_cost": p["avg_cost"], "close": last_close.get(s)}
                for s, p in positions.items()
            ],
            "decisions": rows("SELECT date, symbol, engine, action, confidence, close, outcome, state FROM decisions ORDER BY date DESC, id LIMIT 300"),
            "orders": rows("SELECT * FROM orders ORDER BY id DESC LIMIT 200"),
        }
    finally:
        conn.close()


class Handler(SimpleHTTPRequestHandler):
    db_path = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self.path = "/" + APP_FILE
        elif path == "/api/summary":
            try:
                self.send_json(200, build_summary(self.db_path))
            except (OSError, ValueError, sqlite3.Error) as e:
                self.send_json(500, {"error": str(e)})
            return
        elif path != "/" + APP_FILE:
            # DB や config を直接ダウンロードさせない
            self.send_error(404)
            return
        super().do_GET()

    def send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="ペーパートレードのダッシュボードを起動します")
    parser.add_argument("--port", type=int, default=8768, help="使うポート番号（既定: 8768）")
    parser.add_argument("--db", default=os.path.join(BASE_DIR, "trades.db"), help="表示する SQLite ファイル")
    parser.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")
    args = parser.parse_args()
    Handler.db_path = args.db
    url = f"http://127.0.0.1:{args.port}/"

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        print(f"ポート {args.port} は使用中です。すでに起動している場合はブラウザで {url} を開いてください。")
        if not args.no_browser:
            webbrowser.open(url)
        sys.exit(1)

    print("AI 売買ペーパートレードのダッシュボードを起動しました")
    print(f"  画面: {url}")
    print(f"  記録: {args.db}")
    print("終了するには、このウィンドウを閉じるか Ctrl+C を押してください。")
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
