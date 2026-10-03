import unittest

import risk

R = {
    "min_confidence": 0.6,
    "max_position_yen": 300000,
    "max_exposure_ratio": 0.7,
    "stop_loss_pct": 0.08,
    "max_orders_per_day": 3,
    "daily_loss_limit_yen": 30000,
    "total_loss_limit_yen": 100000,
}


class GlobalBlockTest(unittest.TestCase):
    def test_ok(self):
        self.assertIsNone(risk.global_block(R, 990000, 1000000, 1000000, False))

    def test_stop_file(self):
        self.assertIn("STOP", risk.global_block(R, 1000000, None, 1000000, True))

    def test_total_loss(self):
        self.assertIn("累計", risk.global_block(R, 900000, 905000, 1000000, False))

    def test_daily_loss(self):
        self.assertIn("1日", risk.global_block(R, 960000, 990000, 1000000, False))


class OrderTest(unittest.TestCase):
    def test_low_confidence(self):
        self.assertIn("確信度", risk.check_order(R, "buy", 0.59, 0, 100))

    def test_max_orders(self):
        self.assertIn("上限", risk.check_order(R, "buy", 0.9, 3, 100))

    def test_sell_without_position(self):
        self.assertEqual(risk.check_order(R, "sell", 0.9, 0, 0), "保有していない")

    def test_hold(self):
        self.assertEqual(risk.check_order(R, "hold", 0.9, 0, 0), "hold")

    def test_pass(self):
        self.assertIsNone(risk.check_order(R, "buy", 0.6, 2, 100))


class BuyQtyTest(unittest.TestCase):
    def test_position_cap(self):
        self.assertEqual(risk.buy_qty(R, 1000, 0, 10**7), 300)

    def test_existing_position(self):
        self.assertEqual(risk.buy_qty(R, 1000, 200, 10**7), 100)

    def test_cash_cap(self):
        self.assertEqual(risk.buy_qty(R, 1000, 0, 150000), 100)

    def test_too_expensive(self):
        self.assertEqual(risk.buy_qty(R, 3714, 0, 10**7), 0)


class StopLossTest(unittest.TestCase):
    def test_triggered(self):
        self.assertIn("損切り", risk.stop_loss(R, 920, 1000))

    def test_not_triggered(self):
        self.assertIsNone(risk.stop_loss(R, 921, 1000))

    def test_gain(self):
        self.assertIsNone(risk.stop_loss(R, 1100, 1000))


class ExposureTest(unittest.TestCase):
    def test_room(self):
        self.assertEqual(risk.exposure_room(R, 1000000, 500000), 200000)

    def test_over_limit(self):
        self.assertEqual(risk.exposure_room(R, 1000000, 800000), 0)

    def test_limits_buy(self):
        room = min(900000, risk.exposure_room(R, 1000000, 550000))
        self.assertEqual(risk.buy_qty(R, 1000, 0, room), 100)


class CoreOrderTest(unittest.TestCase):
    # 資産100万円、手元に2%（2万円）残し、5%（5万円）以上ずれたら売買する
    def order(self, cash_free, core_qty=0, price=1000):
        return risk.core_order(cash_free, core_qty, price, 1000000, 0.02, 0.05)

    def test_buys_spare_cash(self):
        self.assertEqual(self.order(500000), ("buy", 480))

    def test_small_surplus_does_nothing(self):
        self.assertIsNone(self.order(60000))

    def test_sells_to_fund_stock_buys(self):
        self.assertEqual(self.order(-100000, core_qty=500), ("sell", 120))

    def test_sell_capped_by_holding(self):
        self.assertEqual(self.order(-100000, core_qty=50), ("sell", 50))

    def test_no_core_to_sell(self):
        self.assertIsNone(self.order(-100000))


if __name__ == "__main__":
    unittest.main()
