"""判断・注文・日次資産を SQLite に記録する。"""
import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  close REAL,
  engine TEXT NOT NULL,
  action TEXT NOT NULL,
  confidence REAL NOT NULL,
  state TEXT NOT NULL,
  raw TEXT,
  outcome TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(date, symbol)
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY,
  decided_on TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty INTEGER NOT NULL,
  status TEXT NOT NULL,          -- pending / filled / rejected / expired
  limit_price REAL,              -- 買い指値（NULL なら翌営業日の寄付きで成行）
  valid_days INTEGER,            -- 指値の有効営業日数
  fill_date TEXT,
  fill_price REAL,
  fee REAL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
  date TEXT PRIMARY KEY,
  cash REAL NOT NULL,
  positions_value REAL NOT NULL,
  equity REAL NOT NULL
);
"""


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
    for col, typ in (("limit_price", "REAL"), ("valid_days", "INTEGER")):
        if col not in cols:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
    return conn


def has_decisions(conn, date):
    return conn.execute("SELECT 1 FROM decisions WHERE date = ? LIMIT 1", (date,)).fetchone() is not None


def add_decision(conn, date, symbol, close, engine, action, confidence, state, raw, outcome):
    conn.execute(
        "INSERT OR IGNORE INTO decisions (date, symbol, close, engine, action, confidence, state, raw, outcome, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (date, symbol, close, engine, action, confidence, state, raw, outcome, datetime.now().isoformat(timespec="seconds")),
    )


def add_order(conn, decided_on, symbol, side, qty, status="pending", note=None, limit_price=None, valid_days=None):
    conn.execute(
        "INSERT INTO orders (decided_on, symbol, side, qty, status, note, limit_price, valid_days) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (decided_on, symbol, side, qty, status, note, limit_price, valid_days),
    )


def pending_orders(conn):
    return conn.execute("SELECT * FROM orders WHERE status = 'pending' ORDER BY id").fetchall()


def fill_order(conn, order_id, fill_date, price, fee):
    conn.execute(
        "UPDATE orders SET status = 'filled', fill_date = ?, fill_price = ?, fee = ? WHERE id = ?",
        (fill_date, price, fee, order_id),
    )


def reject_order(conn, order_id, note, status="rejected"):
    conn.execute("UPDATE orders SET status = ?, note = ? WHERE id = ?", (status, note, order_id))


def pending_buys(conn):
    """未約定の買い指値。{symbol: 確保しておく金額}"""
    out = {}
    for o in conn.execute("SELECT symbol, qty, limit_price FROM orders WHERE status = 'pending' AND side = 'buy' AND limit_price IS NOT NULL"):
        out[o["symbol"]] = out.get(o["symbol"], 0.0) + o["qty"] * o["limit_price"]
    return out


def has_pending(conn, symbol):
    return conn.execute("SELECT 1 FROM orders WHERE status = 'pending' AND symbol = ?", (symbol,)).fetchone() is not None


def cancel_pending_buys(conn, symbol, note):
    conn.execute(
        "UPDATE orders SET status = 'rejected', note = ? WHERE status = 'pending' AND side = 'buy' AND symbol = ?",
        (note, symbol),
    )


def orders_decided_on(conn, date):
    return conn.execute(
        "SELECT COUNT(*) FROM orders WHERE decided_on = ? AND status != 'rejected'", (date,)
    ).fetchone()[0]


def portfolio(conn, initial_cash, tax_rate=0.0):
    """約定済み注文から現金と保有株数（と平均取得単価・保有開始日）を組み立てる。"""
    cash, positions, _ = replay_orders(conn, initial_cash, tax_rate)
    return cash, positions


def realized_by_year(conn):
    """年ごとの確定損益（売却代金 − 手数料 − 取得原価）。"""
    return replay_orders(conn, 0, 0.0)[2]


def replay_orders(conn, initial_cash, tax_rate):
    """約定済み注文を順に積み上げて (現金, 保有, 年ごとの確定損益) を返す。
    税金は特定口座（源泉徴収あり）と同じく、売るたびにその年の確定損益の累計に合わせて
    差し引き、損失が出たら同じ年のうちに払った分を戻す（翌年への繰越はしない）。"""
    cash = float(initial_cash)
    qty, cost, since = {}, {}, {}
    realized = {}
    for o in conn.execute("SELECT * FROM orders WHERE status = 'filled' ORDER BY fill_date, id"):
        amount = o["qty"] * o["fill_price"]
        s = o["symbol"]
        if o["side"] == "buy":
            cash -= amount + o["fee"]
            if not qty.get(s):
                since[s] = o["fill_date"]
            cost[s] = cost.get(s, 0.0) + amount + o["fee"]
            qty[s] = qty.get(s, 0) + o["qty"]
        else:
            basis = cost[s] * o["qty"] / qty[s] if qty.get(s) else 0.0
            cash += amount - o["fee"]
            year = o["fill_date"][:4]
            before = max(0.0, realized.get(year, 0.0)) * tax_rate
            realized[year] = realized.get(year, 0.0) + amount - o["fee"] - basis
            cash -= max(0.0, realized[year]) * tax_rate - before
            if qty.get(s):
                cost[s] -= basis
            qty[s] = qty.get(s, 0) - o["qty"]
    positions = {s: {"qty": q, "avg_cost": cost[s] / q, "since": since[s]} for s, q in qty.items() if q > 0}
    return cash, positions, realized


def save_snapshot(conn, date, cash, positions_value):
    conn.execute(
        "INSERT OR REPLACE INTO snapshots (date, cash, positions_value, equity) VALUES (?, ?, ?, ?)",
        (date, cash, positions_value, cash + positions_value),
    )


def previous_equity(conn, date):
    row = conn.execute(
        "SELECT equity FROM snapshots WHERE date < ? ORDER BY date DESC LIMIT 1", (date,)
    ).fetchone()
    return row["equity"] if row else None
