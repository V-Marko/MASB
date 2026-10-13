import json
import threading
from pathlib import Path

from config import DATA_DIR


FILE_PATH = Path(DATA_DIR) / "notification_settings.json"
LOCK = threading.Lock()

DEFAULT_SETTINGS = {
    "signals_enabled": True,
}


def _read_settings():
    FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not FILE_PATH.exists():
        return DEFAULT_SETTINGS.copy()

    try:
        with FILE_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return DEFAULT_SETTINGS.copy()

        return {
            "signals_enabled": bool(
                data.get("signals_enabled", True)
            )
        }

    except (OSError, json.JSONDecodeError):
        return DEFAULT_SETTINGS.copy()


def _write_settings(settings):
    FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    temp_path = FILE_PATH.with_suffix(".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(
            settings,
            file,
            ensure_ascii=False,
            indent=2,
        )

    temp_path.replace(FILE_PATH)


def notifications_enabled():
    with LOCK:
        return _read_settings()["signals_enabled"]


def set_notifications(enabled: bool):
    with LOCK:
        settings = _read_settings()
        settings["signals_enabled"] = bool(enabled)
        _write_settings(settings)

        return settings["signals_enabled"]


def toggle_notifications():
    with LOCK:
        settings = _read_settings()
        settings["signals_enabled"] = not settings[
            "signals_enabled"
        ]
        _write_settings(settings)

        return settings["signals_enabled"]