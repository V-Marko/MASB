import unittest

from services.trade_monitor import close_trade, entry_hit, touched_exit


class TradeMonitorTests(unittest.TestCase):
    def setUp(self):
        self.trade = {
            "symbol": "BTCUSDT",
            "strategy": "fibZZ10",
            "direction": "LONG",
            "status": "OPEN",
            "entry": 100.0,
            "tp": 110.0,
            "sl": 95.0,
            "quantity": 0.5,
        }

    def test_long_entry_detected_by_low(self):
        self.assertTrue(entry_hit(self.trade, {"low": 99.0, "high": 103.0}))
        self.assertFalse(entry_hit(self.trade, {"low": 101.0, "high": 103.0}))

    def test_same_candle_tp_and_sl_is_sl_first(self):
        candle = {"low": 94.0, "high": 111.0}
        self.assertEqual(touched_exit(self.trade, candle), "SL")

    def test_tp_only_is_tp(self):
        self.assertEqual(touched_exit(self.trade, {"low": 99.0, "high": 111.0}), "TP")

    def test_short_levels(self):
        trade = {**self.trade, "direction": "SHORT", "entry": 100.0, "tp": 90.0, "sl": 105.0}
        self.assertTrue(entry_hit(trade, {"low": 98.0, "high": 101.0}))
        self.assertEqual(touched_exit(trade, {"low": 89.0, "high": 106.0}), "SL")

    def test_close_trade_accounts_for_fees(self):
        result = close_trade(self.trade, "TP", 110.0)
        self.assertAlmostEqual(result["pnl_gross"], 5.0)
        self.assertGreater(result["fees"], 0.0)
        self.assertLess(result["pnl_net"], result["pnl_gross"])


if __name__ == "__main__":
    unittest.main()
