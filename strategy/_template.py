"""Шаблон новой стратегии. Скопируй в strategy/моё_имя.py и заполни.

Файлы, начинающиеся с "_", ботом игнорируются.
"""
from __future__ import annotations

from typing import Any, Sequence

NAME = "my_strategy"                 # уникальное имя
DESCRIPTION = "Моя стратегия"        # как показывать в меню


def analyze(
    candles: Sequence[dict[str, Any]],
    current_price: float | None = None,
    capital: float = 100.0,
    open_positions: int = 0,
    pending_orders: int = 0,
) -> dict[str, Any] | None:
    # candles: dict с timestamp, open, high, low, close; по возрастанию времени.
    # Нет сетапа -> return None.
    return None


def format_signal(signal: dict[str, Any] | None) -> str:
    if signal is None:
        return f"{NAME}: нет сетапа."
    return f"{NAME}: {signal['direction']}"
