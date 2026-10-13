
from typing import Any


def fibo_price(
    swing_start: float,
    swing_end: float,
    level: float,
) -> float:


    swing_start = float(swing_start)
    swing_end = float(swing_end)
    level = float(level)

    if swing_start <= 0 or swing_end <= 0:
        raise ValueError("Цены свинга должны быть больше нуля.")

    if swing_start == swing_end:
        raise ValueError("Невозможно построить Fibonacci без движения.")

    return swing_end - (swing_end - swing_start) * level


def fibonacci_levels(
    swing_start: float,
    swing_end: float,
    levels: tuple[float, ...] = (
        0.0,
        0.382,
        0.5,
        0.618,
        0.805,
        1.0,
    ),
) -> dict[float, float]:

    return {
        float(level): fibo_price(
            swing_start,
            swing_end,
            level,
        )
        for level in levels
    }


def build_fibonacci_setup(
    swing_start: float,
    swing_end: float,
    entry_fib: float = 0.805,
    tp_fib: float = 0.5,
) -> dict[str, Any] | None:
    swing_start = float(swing_start)
    swing_end = float(swing_end)

    if swing_start <= 0 or swing_end <= 0:
        raise ValueError("Цены свинга должны быть больше нуля.")

    if swing_start == swing_end:
        return None

    direction = "LONG" if swing_start < swing_end else "SHORT"

    entry = fibo_price(
        swing_start,
        swing_end,
        entry_fib,
    )

    tp = fibo_price(
        swing_start,
        swing_end,
        tp_fib,
    )

    # Для LONG TP должен быть выше Entry.
    # Для SHORT TP должен быть ниже Entry.
    if direction == "LONG" and tp <= entry:
        return None

    if direction == "SHORT" and tp >= entry:
        return None

    return {
        "direction": direction,
        "entry": entry,
        "tp": tp,
        "entry_fib": float(entry_fib),
        "tp_fib": float(tp_fib),
        "swing_start": swing_start,
        "swing_end": swing_end,
    }
