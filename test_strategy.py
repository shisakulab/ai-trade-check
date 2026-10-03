import unittest

import main

CFG = {"risk": {"max_position_yen": 300000}, "screen": {"top_n": 2}}


def f(close, sma75, chg_60d):
    return {"close": close, "sma75": sma75, "chg_60d": chg_60d}


class MomentumTest(unittest.TestCase):
    def test_ranks_uptrend_by_60d_change(self):
        feats = {
            "A": f(100, 90, 0.10),
            "B": f(100, 90, 0.30),
            "C": f(100, 110, 0.50),   # 75日線より下なので除外
            "D": f(4000, 3000, 0.90),  # 100株で30万円を超えるので除外
            "E": f(100, 90, 0.20),
            "F": f(100, 90, 0.05),
            "G": f(100, 90, 0.01),
        }
        keep, top = main.momentum_keep(CFG, feats)
        self.assertEqual(top, ["B", "E"])
        self.assertEqual(keep, {"B", "E", "A", "F"})


class RebalanceDayTest(unittest.TestCase):
    def test_first_trading_day_of_week(self):
        self.assertTrue(main.is_rebalance_day("2026-09-28", "2026-09-25"))   # 金→月
        self.assertTrue(main.is_rebalance_day("2026-09-22", "2026-09-18"))   # 月祝→火
        self.assertFalse(main.is_rebalance_day("2026-09-29", "2026-09-28"))
        self.assertTrue(main.is_rebalance_day("2026-09-29", None))


if __name__ == "__main__":
    unittest.main()
