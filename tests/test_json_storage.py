import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from core.json_storage import CorruptJsonError, JsonStorage, JsonStorageError


class JsonStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = JsonStorage(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_initial_files_are_created(self):
        self.assertEqual(self.storage.load("config")["virtual_balance"], "100")
        self.assertTrue((Path(self.temp_dir.name) / "config.json").exists())

    def test_decimal_is_saved_as_string_and_float_is_not_used_for_money(self):
        self.storage.save("trade_history", {"amount": Decimal("1.2300")})
        self.assertEqual(json.loads((Path(self.temp_dir.name) / "trade_history.json").read_text(encoding="utf-8")), {"amount": "1.2300"})

    def test_important_file_backup_is_created_before_overwrite(self):
        self.storage.save("realtime_state", {"positions": [{"id": "p1"}]})
        self.storage.save("realtime_state", {"positions": [{"id": "p2"}]})
        backup = Path(self.temp_dir.name) / "realtime_state.json.bak"
        self.assertTrue(backup.exists())
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["positions"][0]["id"], "p1")

    def test_corrupt_file_is_quarantined_not_silently_reset(self):
        path = Path(self.temp_dir.name) / "open_orders.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(CorruptJsonError):
            self.storage.load("open_orders")
        self.assertFalse(path.exists())
        self.assertTrue(list(Path(self.temp_dir.name).glob("open_orders.json.corrupt_*")))

    def test_corrupt_file_recovers_from_valid_backup_and_reports_it(self):
        path = Path(self.temp_dir.name) / "realtime_state.json"
        self.storage.save("realtime_state", {"positions": [{"id": "safe"}]})
        path.with_suffix(".json.bak").write_text('{"positions":[{"id":"backup"}]}', encoding="utf-8")
        path.write_text("not json", encoding="utf-8")
        with self.assertRaises(JsonStorageError):
            self.storage.load("realtime_state")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["positions"][0]["id"], "backup")

    def test_unknown_key_fails_clearly(self):
        with self.assertRaises(KeyError):
            self.storage.load("unknown")


if __name__ == "__main__":
    unittest.main()
