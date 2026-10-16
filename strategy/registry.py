from __future__ import annotations

import importlib
import logging
import pkgutil
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("MASB.Registry")

_PACKAGE_DIR = Path(__file__).resolve().parent
_SKIP = {"registry"}


def _default_format(signal: dict[str, Any] | None) -> str:
    if signal is None:
        return "Нет нового подходящего сетапа."
    return f"Сигнал: {signal.get('strategy', '?')} {signal.get('direction', '')}"


def _describe(module: Any, name: str) -> str:
    description = getattr(module, "DESCRIPTION", None)
    if isinstance(description, str) and description.strip():
        return description.strip()

    deviation = getattr(module, "ZIGZAG_DEVIATION", None)
    if isinstance(deviation, (int, float)):
        return f"ZigZag {deviation:g}% + Fibonacci"

    return name


@lru_cache(maxsize=1)
def discover_strategies() -> tuple[
    dict[str, Callable[..., Any]],
    dict[str, Callable[[dict[str, Any] | None], str]],
    dict[str, str],
]:
    analyzers: dict[str, Callable[..., Any]] = {}
    formatters: dict[str, Callable[[dict[str, Any] | None], str]] = {}
    descriptions: dict[str, str] = {}

    modules = sorted(
        info.name
        for info in pkgutil.iter_modules([str(_PACKAGE_DIR)])
        if not info.name.startswith("_") and info.name not in _SKIP
    )

    for module_name in modules:
        try:
            module = importlib.import_module(f"strategy.{module_name}")
        except Exception:
            logger.exception("Не удалось загрузить стратегию %s", module_name)
            continue

        name = getattr(module, "NAME", None)
        analyze = getattr(module, "analyze", None)

        if not isinstance(name, str) or not name.strip() or not callable(analyze):
            logger.warning(
                "Файл strategy/%s.py пропущен: нужны NAME и analyze().",
                module_name,
            )
            continue

        name = name.strip()

        if name in analyzers:
            logger.warning(
                "Стратегия %s из %s.py пропущена: такое NAME уже есть.",
                name,
                module_name,
            )
            continue

        analyzers[name] = analyze
        formatter = getattr(module, "format_signal", None)
        formatters[name] = formatter if callable(formatter) else _default_format
        descriptions[name] = _describe(module, name)

    logger.info("Загружены стратегии: %s", ", ".join(analyzers) or "нет")
    return analyzers, formatters, descriptions
