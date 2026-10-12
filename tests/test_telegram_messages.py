import unittest

from telegram_bot.messages import balance_message, help_message, status_message, strategies_message


class TelegramMessageTests(unittest.TestCase):
    def test_status_marks_realtime_as_not_running(self):
        text = status_message({"virtual_balance": "100", "leverage": 10}, {"running": False})
        self.assertIn("не запущена", text)
        self.assertIn("100 USDT", text)
        self.assertIn("не подключены", text)

    def test_balance_explains_starting_balance_is_not_equity(self):
        text = balance_message({"virtual_balance": "100", "leverage": 10, "capital_mode": "shared"})
        self.assertIn("100 USDT", text)
        self.assertIn("общий баланс стратегий", text)
        self.assertIn("не расчёт текущего equity", text)

    def test_strategies_are_not_claimed_to_be_implemented(self):
        text = strategies_message({"active_strategies": []})
        self.assertIn("ещё не реализована", text)
        self.assertIn("ZigZag 10%", text)

    def test_help_contains_supported_commands_only(self):
        text = help_message()
        for command in ("/start", "/status", "/balance", "/strategies", "/help"):
            self.assertIn(command, text)
        self.assertNotIn("/backtest", text)


if __name__ == "__main__":
    unittest.main()
