import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR


FILE_PATH = Path(DATA_DIR) / "signal_history.json"
LOCK = threading.Lock()
MAX_SIGNALS = 200


def _read_all():
    FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not FILE_PATH.exists():
        return []

    try:
        with FILE_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, list) else []

    except (OSError, json.JSONDecodeError):
        return []


def _write_all(signals):
    FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = FILE_PATH.with_suffix(".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(
            signals[-MAX_SIGNALS:],
            file,
            ensure_ascii=False,
            indent=2,
        )

    temp_path.replace(FILE_PATH)


def record_signal(
    strategy: str,
    symbol: str,
    direction: str,
    price: float,
    entry: float,
    tp: float,
    sl: float,
    rr: float = 3.0,
):
    signal = {
        "time": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "strategy": str(strategy),
        "symbol": str(symbol),
        "direction": str(direction).upper(),
        "price": float(price),
        "entry": float(entry),
        "tp": float(tp),
        "sl": float(sl),
        "rr": float(rr),
    }

    with LOCK:
        signals = _read_all()
        signals.append(signal)
        _write_all(signals)

    return signal


def get_signals(limit: int = 10):
    with LOCK:
        signals = _read_all()

    return list(reversed(signals[-max(1, limit):]))


def clear_signals():
    with LOCK:
        _write_all([])