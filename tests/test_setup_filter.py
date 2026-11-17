import unittest

from core.setup_filter import filter_signal_orders


class SetupFilterTests(unittest.TestCase):
    def signal(self, direction="LONG", entry=98.0, tp=107.0, sl=95.0):
        return {
            "strategy": "unit-test",
            "direction": direction,
            "current_price": 100.0,
            "orders": [{"entry": entry, "tp": tp, "sl": sl}],
        }

    def test_accepts_entry_within_five_percent_and_good_rr(self):
        result, rejected = filter_signal_orders(self.signal(), 100.0)
        self.assertIsNotNone(result)
        self.assertFalse(rejected)
        self.assertAlmostEqual(result["orders"][0]["entry_distance_pct"], 2.0)
        self.assertGreaterEqual(result["orders"][0]["actual_rr"], 1.5)

    def test_rejects_entry_beyond_five_percent(self):
        result, rejected = filter_signal_orders(self.signal(entry=94.9, tp=106, sl=90), 100)
        self.assertIsNone(result)
        self.assertTrue(any("дальше лимита" in reason for reason in rejected))

    def test_accepts_exactly_five_percent(self):
        result, _ = filter_signal_orders(self.signal(entry=95, tp=110, sl=90), 100)
        self.assertIsNotNone(result)

    def test_rejects_long_entry_above_market(self):
        result, rejected = filter_signal_orders(self.signal(entry=101, tp=110, sl=98), 100)
        self.assertIsNone(result)
        self.assertTrue(any("LONG Entry выше" in reason for reason in rejected))

    def test_rejects_short_entry_below_market(self):
        signal = self.signal("SHORT", entry=99, tp=90, sl=102)
        result, rejected = filter_signal_orders(signal, 100)
        self.assertIsNone(result)
        self.assertTrue(any("SHORT Entry ниже" in reason for reason in rejected))

    def test_valid_short_order(self):
        signal = self.signal("SHORT", entry=102, tp=93, sl=105)
        result, rejected = filter_signal_orders(signal, 100)
        self.assertIsNotNone(result)
        self.assertFalse(rejected)

    def test_rejects_low_reward_risk(self):
        result, rejected = filter_signal_orders(self.signal(entry=99, tp=100.4, sl=98), 100)
        self.assertIsNone(result)
        self.assertTrue(any("RR" in reason for reason in rejected))

    def test_filters_individual_orders_without_dropping_valid_ones(self):
        signal = self.signal()
        signal["orders"].append({"entry": 90, "tp": 110, "sl": 85})
        result, rejected = filter_signal_orders(signal, 100)
        self.assertIsNotNone(result)
        self.assertEqual(len(result["orders"]), 1)
        self.assertTrue(rejected)


if __name__ == "__main__":
    unittest.main()
