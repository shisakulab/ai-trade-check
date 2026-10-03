import unittest

import pandas as pd

import store
from broker import paper

COSTS = {"commission_rate": 0.0, "slippage_rate": 0.001}


def bars(rows):
    """rows: [(date, open, low)]"""
    df = pd.DataFrame([{"Open": o, "High": o, "Low": l, "Close": o} for _, o, l in rows], index=[d for d, _, _ in rows])
    return {"X": df}


class LimitBuyTest(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(":memory:")
        store.add_order(self.conn, "2026-09-01", "X", "buy", 100, limit_price=99.0, valid_days=2)

    def order(self):
        return self.conn.execute("SELECT * FROM orders").fetchone()

    def test_fills_at_limit_when_low_crosses(self):
        b = bars([("2026-09-01", 100, 99), ("2026-09-02", 100, 98.5)])
        paper.settle(self.conn, b, COSTS, 1000000, "2026-09-02")
        o = self.order()
        self.assertEqual((o["status"], o["fill_date"], o["fill_price"]), ("filled", "2026-09-02", 99.0))

    def test_fills_at_open_on_gap_down(self):
        b = bars([("2026-09-01", 100, 99), ("2026-09-02", 97, 96)])
        paper.settle(self.conn, b, COSTS, 1000000, "2026-09-02")
        self.assertEqual(self.order()["fill_price"], 97.0)

    def test_touching_limit_is_not_a_fill(self):
        b = bars([("2026-09-01", 100, 99), ("2026-09-02", 100, 99.0)])
        paper.settle(self.conn, b, COSTS, 1000000, "2026-09-02")
        self.assertEqual(self.order()["status"], "pending")

    def test_second_day_fill(self):
        b = bars([("2026-09-01", 100, 99), ("2026-09-02", 100, 99.5), ("2026-09-03", 99.5, 98)])
        paper.settle(self.conn, b, COSTS, 1000000, "2026-09-02")
        paper.settle(self.conn, b, COSTS, 1000000, "2026-09-03")
        self.assertEqual(self.order()["fill_date"], "2026-09-03")

    def test_expires_after_valid_days(self):
        b = bars([("2026-09-01", 100, 99), ("2026-09-02", 100, 99.5), ("2026-09-03", 101, 100), ("2026-09-04", 90, 89)])
        paper.settle(self.conn, b, COSTS, 1000000, "2026-09-04")
        self.assertEqual(self.order()["status"], "expired")

    def test_pending_buys_reserve_cash(self):
        self.assertEqual(store.pending_buys(self.conn), {"X": 9900.0})


class MarketOrderTest(unittest.TestCase):
    def test_next_open_with_slippage(self):
        conn = store.connect(":memory:")
        store.add_order(conn, "2026-09-01", "X", "buy", 100)
        paper.settle(conn, bars([("2026-09-01", 100, 99), ("2026-09-02", 100, 90)]), COSTS, 1000000, "2026-09-02")
        self.assertAlmostEqual(conn.execute("SELECT fill_price FROM orders").fetchone()[0], 100.1)


class SettleOrderTest(unittest.TestCase):
    def test_sell_proceeds_fund_same_day_buy(self):
        conn = store.connect(":memory:")
        # 現金10,000円で X を100株（約10,000円）持っている状態から、Y を買って X を売る（買いのほうが先の注文）
        store.add_order(conn, "2026-08-31", "X", "buy", 100)
        b = {
            "X": bars([("2026-08-31", 100, 100), ("2026-09-01", 100, 100), ("2026-09-02", 100, 100)])["X"],
            "Y": bars([("2026-09-01", 150, 150), ("2026-09-02", 150, 150)])["X"],
        }
        paper.settle(conn, b, {"commission_rate": 0.0, "slippage_rate": 0.0}, 10000, "2026-09-01")
        store.add_order(conn, "2026-09-01", "Y", "buy", 50)
        store.add_order(conn, "2026-09-01", "X", "sell", 100)
        paper.settle(conn, b, {"commission_rate": 0.0, "slippage_rate": 0.0}, 10000, "2026-09-02")
        statuses = [r[0] for r in conn.execute("SELECT status FROM orders ORDER BY id")]
        self.assertEqual(statuses, ["filled", "filled", "filled"])


class TaxTest(unittest.TestCase):
    def fill(self, conn, side, qty, date, price):
        store.add_order(conn, date, "X", side, qty)
        oid = conn.execute("SELECT MAX(id) FROM orders").fetchone()[0]
        store.fill_order(conn, oid, date, price, 0.0)

    def test_gain_is_taxed_and_loss_refunds_within_year(self):
        conn = store.connect(":memory:")
        self.fill(conn, "buy", 200, "2025-01-06", 1000)
        self.fill(conn, "sell", 100, "2025-02-03", 1200)   # +20,000円 → 税 4,063円
        cash, _ = store.portfolio(conn, 1000000, 0.20315)
        self.assertAlmostEqual(cash, 1000000 - 200000 + 120000 - 4063)
        self.fill(conn, "sell", 100, "2025-03-03", 900)    # −10,000円 → 通算 +10,000円、2,031.5円戻る
        cash, _ = store.portfolio(conn, 1000000, 0.20315)
        self.assertAlmostEqual(cash, 1000000 - 200000 + 120000 + 90000 - 2031.5)
        self.assertEqual(store.realized_by_year(conn), {"2025": 10000.0})

    def test_years_are_separate(self):
        conn = store.connect(":memory:")
        self.fill(conn, "buy", 200, "2025-01-06", 1000)
        self.fill(conn, "sell", 100, "2025-12-01", 1100)   # 2025年 +10,000円
        self.fill(conn, "sell", 100, "2026-01-05", 900)    # 2026年 −10,000円（前年の税は戻らない）
        cash, _ = store.portfolio(conn, 1000000, 0.20315)
        self.assertAlmostEqual(cash, 1000000 - 200000 + 110000 + 90000 - 2031.5)


if __name__ == "__main__":
    unittest.main()
