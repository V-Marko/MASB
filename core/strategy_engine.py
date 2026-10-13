
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence


# ============================================================
# MASB STRATEGY ENGINE
# ZigZag + Fibonacci + Risk Management
# No database. No real orders.
# ============================================================

SwingType = Literal["LOW", "HIGH"]
Direction = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class Candle:
    index: int
    open: float
    high: float
    low: float
    close: float
    timestamp: Any = None


@dataclass(frozen=True)
class Swing:
    index: int
    price: float
    kind: SwingType
    confirmed_at: int


@dataclass(frozen=True)
class TradeSetup:
    direction: Direction
    entry: float
    tp: float
    sl: float
    rr: float
    entry_fib: float
    tp_fib: float
    swing_start: float
    swing_end: float


# ============================================================
# VALIDATION
# ============================================================

def _positive_number(value: float, name: str) -> float:
    value = float(value)

    if value <= 0:
        raise ValueError(f"{name} должен быть больше нуля.")

    return value


def normalize_candles(
    candles: Sequence[Candle | dict],
) -> list[Candle]:
    result: list[Candle] = []

    for index, item in enumerate(candles):
        if isinstance(item, Candle):
            candle = item
        else:
            candle = Candle(
                index=index,
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                timestamp=item.get("timestamp", item.get("time")),
            )

        if candle.low <= 0 or candle.high <= 0:
            raise ValueError("High и Low должны быть больше нуля.")

        if candle.high < candle.low:
            raise ValueError("High не может быть меньше Low.")

        if not (
            candle.low <= candle.open <= candle.high
            and candle.low <= candle.close <= candle.high
        ):
            raise ValueError("Некорректная OHLC-свеча.")

        result.append(
            Candle(
                index=index,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                timestamp=candle.timestamp,
            )
        )

    return result


# ============================================================
# ZIGZAG
# deviation_percent = 10 means 10%
# Uses candle highs/lows and confirms a swing only after
# a reversal of the configured percentage.
# ============================================================

def find_confirmed_swings(
    candles: Sequence[Candle | dict],
    deviation_percent: float = 10.0,
) -> list[Swing]:
    candles = normalize_candles(candles)

    deviation_percent = _positive_number(
        deviation_percent,
        "deviation_percent",
    )

    if deviation_percent >= 100:
        raise ValueError(
            "Отклонение ZigZag должно быть меньше 100%."
        )

    if len(candles) < 2:
        return []

    deviation = deviation_percent / 100.0

    direction = 0
    candidate_high = candles[0].high
    candidate_high_index = 0

    candidate_low = candles[0].low
    candidate_low_index = 0

    swings: list[Swing] = []

    for i in range(1, len(candles)):
        candle = candles[i]

        if direction == 0:
            # Пока направление не установлено,
            # запоминаем экстремумы.
            if candle.high > candidate_high:
                candidate_high = candle.high
                candidate_high_index = i

            if candle.low < candidate_low:
                candidate_low = candle.low
                candidate_low_index = i

            rise = (
                candidate_high / candidate_low - 1.0
                if candidate_low > 0
                else 0.0
            )

            fall = (
                candidate_high / candidate_low - 1.0
                if candidate_high > 0
                else 0.0
            )

            if rise >= deviation:
                # Минимум подтверждается движением вверх.
                swings.append(
                    Swing(
                        index=candidate_low_index,
                        price=candidate_low,
                        kind="LOW",
                        confirmed_at=i,
                    )
                )

                direction = 1
                candidate_high = candle.high
                candidate_high_index = i

            elif fall >= deviation:
                # Максимум подтверждается движением вниз.
                swings.append(
                    Swing(
                        index=candidate_high_index,
                        price=candidate_high,
                        kind="HIGH",
                        confirmed_at=i,
                    )
                )

                direction = -1
                candidate_low = candle.low
                candidate_low_index = i

            continue

        if direction == 1:
            # Восходящий участок: обновляем максимум.
            if candle.high > candidate_high:
                candidate_high = candle.high
                candidate_high_index = i

            # Максимум подтверждается обратным движением.
            if candle.low <= candidate_high * (1.0 - deviation):
                swings.append(
                    Swing(
                        index=candidate_high_index,
                        price=candidate_high,
                        kind="HIGH",
                        confirmed_at=i,
                    )
                )

                direction = -1
                candidate_low = candle.low
                candidate_low_index = i

        else:
            # Нисходящий участок: обновляем минимум.
            if candle.low < candidate_low:
                candidate_low = candle.low
                candidate_low_index = i

            # Минимум подтверждается обратным движением.
            if candle.high >= candidate_low * (1.0 + deviation):
                swings.append(
                    Swing(
                        index=candidate_low_index,
                        price=candidate_low,
                        kind="LOW",
                        confirmed_at=i,
                    )
                )

                direction = 1
                candidate_high = candle.high
                candidate_high_index = i

    return swings


# ============================================================
# FIBONACCI
#
# For an upward swing LOW -> HIGH:
#   fib 0.0 = HIGH
#   fib 0.5 = midpoint
#   fib 1.0 = LOW
#
# For a downward swing HIGH -> LOW:
#   fib 0.0 = LOW
#   fib 0.5 = midpoint
#   fib 1.0 = HIGH
#
# Levels greater than 1.0 are also supported.
# ============================================================

def fibo_price(
    swing_start: float,
    swing_end: float,
    level: float,
) -> float:
    swing_start = _positive_number(swing_start, "swing_start")
    swing_end = _positive_number(swing_end, "swing_end")
    level = float(level)

    if swing_start == swing_end:
        raise ValueError("Свинг должен иметь ненулевую амплитуду.")

    # Например, LOW=100, HIGH=120:
    # level 0.5 -> 110
    # level 0.8 -> 104
    return swing_end - (swing_end - swing_start) * level


def fibonacci_levels(
    swing_start: float,
    swing_end: float,
    levels: Sequence[float] = (
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
            float(level),
        )
        for level in levels
    }


# ============================================================
# RISK MANAGEMENT
# RR = 1:3 means reward is 3 times the Entry-to-SL distance.
#
# LONG:
#   SL = Entry - (TP - Entry) / RR
#
# SHORT:
#   SL = Entry + (Entry - TP) / RR
# ============================================================

def calculate_sl(
    entry: float,
    tp: float,
    rr: float = 3.0,
    direction: Direction = "LONG",
) -> float:
    entry = _positive_number(entry, "entry")
    tp = _positive_number(tp, "tp")
    rr = _positive_number(rr, "rr")

    if direction == "LONG":
        if tp <= entry:
            raise ValueError(
                "Для LONG TP должен быть выше Entry."
            )

        reward = tp - entry
        return entry - reward / rr

    if direction == "SHORT":
        if tp >= entry:
            raise ValueError(
                "Для SHORT TP должен быть ниже Entry."
            )

        reward = entry - tp
        return entry + reward / rr

    raise ValueError("direction должен быть LONG или SHORT.")


# ============================================================
# CREATE TRADE SETUP FROM FIBONACCI LEVELS
# ============================================================

def build_trade_setup(
    swing_start: float,
    swing_end: float,
    entry_fib: float = 0.805,
    tp_fib: float = 0.5,
    rr: float = 3.0,
) -> TradeSetup:
    swing_start = _positive_number(
        swing_start,
        "swing_start",
    )
    swing_end = _positive_number(
        swing_end,
        "swing_end",
    )

    if swing_start == swing_end:
        raise ValueError("Невозможно построить Fibo без движения.")

    if swing_start < swing_end:
        # Последний свинг был LOW -> HIGH.
        direction: Direction = "LONG"
    else:
        # Последний свинг был HIGH -> LOW.
        direction = "SHORT"

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

    sl = calculate_sl(
        entry=entry,
        tp=tp,
        rr=rr,
        direction=direction,
    )

    return TradeSetup(
        direction=direction,
        entry=entry,
        tp=tp,
        sl=sl,
        rr=rr,
        entry_fib=float(entry_fib),
        tp_fib=float(tp_fib),
        swing_start=swing_start,
        swing_end=swing_end,
    )


# ============================================================
# CREATE SETUP FROM THE LAST CONFIRMED ZIGZAG SWING
# ============================================================

def setup_from_zigzag(
    candles: Sequence[Candle | dict],
    deviation_percent: float = 10.0,
    entry_fib: float = 0.805,
    tp_fib: float = 0.5,
    rr: float = 3.0,
) -> TradeSetup | None:
    swings = find_confirmed_swings(
        candles,
        deviation_percent=deviation_percent,
    )

    if len(swings) < 2:
        return None

    first = swings[-2]
    second = swings[-1]

    # Для обычного ZigZag свинги должны чередоваться.
    if first.kind == second.kind:
        return None

    return build_trade_setup(
        swing_start=first.price,
        swing_end=second.price,
        entry_fib=entry_fib,
        tp_fib=tp_fib,
        rr=rr,
    )


# ============================================================
# FORMAT FOR TELEGRAM
# ============================================================

def format_trade_setup(setup: TradeSetup) -> str:
    return (
        f"📐 Fibonacci + ZigZag\n\n"
        f"Направление: {setup.direction}\n"
        f"Entry Fibo: {setup.entry_fib:g}\n"
        f"TP Fibo: {setup.tp_fib:g}\n"
        f"RR: 1:{setup.rr:g}\n\n"
        f"Entry: {setup.entry:.8f}\n"
        f"TP: {setup.tp:.8f}\n"
        f"SL: {setup.sl:.8f}\n\n"
        f"Свинг: {setup.swing_start:.8f} → "
        f"{setup.swing_end:.8f}"
    )
