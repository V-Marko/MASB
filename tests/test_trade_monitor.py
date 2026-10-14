import unittest

from services.trade_monitor import close_trade, entry_hit, touched_exit, render_trade_chart


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

    def test_stop_loss_chart_is_generated_with_exit_marker_data(self):
        from datetime import datetime, timezone
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        candles = []
        for i in range(40):
            base = 100 + (i % 7) * 0.25
            candles.append({
                "timestamp": now_ms - (39 - i) * 3_600_000,
                "open": base,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base + 0.25,
                "volume": 100,
            })
        trade = {
            **self.trade,
            "symbol": "BTCUSDT",
            "strategy": "fibZZ10",
            "status": "SL",
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "exit_price": 95.0,
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "pnl_net": -2.75,
            "entry_number": 1,
            "swing_start": 95.0,
            "swing_end": 105.0,
            "swing_start_index": 10,
            "swing_end_index": 20,
            "swing_start_timestamp": candles[10]["timestamp"],
            "swing_end_timestamp": candles[20]["timestamp"],
            "fibonacci": {"0.618": 101.0},
            "green_fibonacci": {"0.382": 108.0},
        }
        chart = render_trade_chart(candles, trade, "SL")
        self.assertEqual(chart.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertIn("sl", chart.name.lower())


if __name__ == "__main__":
    unittest.main()
