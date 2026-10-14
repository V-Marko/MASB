from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from config import DATA_DIR


logger = logging.getLogger("MASB.StrategyManager")

STATE_FILE = DATA_DIR / "strategy_manager.json"

DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "HYPEUSDT",
    "AAVEUSDT",
    "AVAXUSDT",
    "PNUTUSDT",
    "XRPUSDT",
    "FARTCOINUSDT",
    "IMXUSDT",
    "SUIUSDT",
    "NEARUSDT",
    "LINKUSDT",
    "1000PEPEUSDT",
    "DOGEUSDT",
    "LTCUSDT",
    "SEIUSDT",
    "ARBUSDT",
    "POPCATUSDT",
    "ENAUSDT",
    "APTUSDT",
]

_LOCK = threading.RLock()


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "symbols": DEFAULT_SYMBOLS,
        "strategies": {
            "fibZZ10": {
                "enabled": False,
                "last_candle": {},
                "signals": 0,
                "last_error": None,
            }
        },
    }


def _read_state() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not STATE_FILE.exists():
        state = _default_state()
        _write_state(state)
        return state

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            state = json.load(file)

        if not isinstance(state, dict):
            raise ValueError("Корневое значение JSON должно быть объектом")

        defaults = _default_state()
        state.setdefault("schema_version", 1)
        state.setdefault("symbols", DEFAULT_SYMBOLS)
        state.setdefault("strategies", {})

        for name, default in defaults["strategies"].items():
            state["strategies"].setdefault(name, default.copy())

        return state

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        # Не затираем повреждённый файл автоматически.
        raise RuntimeError(
            f"Не удалось прочитать {STATE_FILE}: {exc}"
        ) from exc


def _write_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = STATE_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)

    temp_file.replace(STATE_FILE)


def list_strategies() -> dict[str, dict[str, Any]]:
    with _LOCK:
        return _read_state()["strategies"]


def set_strategy_enabled(name: str, enabled: bool) -> None:
    with _LOCK:
        state = _read_state()

        if name not in state["strategies"]:
            raise ValueError(f"Стратегия не зарегистрирована: {name}")

        state["strategies"][name]["enabled"] = bool(enabled)
        _write_state(state)


def set_last_candle(name: str, symbol: str, timestamp: int) -> None:
    with _LOCK:
        state = _read_state()
        strategy = state["strategies"][name]
        strategy.setdefault("last_candle", {})[symbol] = int(timestamp)
        _write_state(state)


def get_last_candle(name: str, symbol: str) -> int | None:
    with _LOCK:
        state = _read_state()
        value = (
            state["strategies"]
            .get(name, {})
            .get("last_candle", {})
            .get(symbol)
        )
        return int(value) if value is not None else None


def record_signal(name: str) -> None:
    with _LOCK:
        state = _read_state()
        item = state["strategies"][name]
        item["signals"] = int(item.get("signals", 0)) + 1
        item["last_error"] = None
        _write_state(state)


def record_error(name: str, message: str) -> None:
    with _LOCK:
        state = _read_state()
        if name in state["strategies"]:
            state["strategies"][name]["last_error"] = message[:1000]
            _write_state(state)


def get_symbols() -> list[str]:
    with _LOCK:
        state = _read_state()
        symbols = state.get("symbols", DEFAULT_SYMBOLS)

    if not isinstance(symbols, list):
        return DEFAULT_SYMBOLS.copy()

    return list(dict.fromkeys(
        str(symbol).strip().upper()
        for symbol in symbols
        if str(symbol).strip()
    ))


def add_strategy(name: str) -> None:
    with _LOCK:
        state = _read_state()

        if name in state["strategies"]:
            raise ValueError(f"Стратегия уже существует: {name}")

        state["strategies"][name] = {
            "enabled": False,
            "last_candle": {},
            "signals": 0,
            "last_error": None,
        }

        _write_state(state)