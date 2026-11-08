from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from core.zigzag import find_confirmed_swings
from core.fibonacci import fibo_price, fibonacci_levels
from core.risk_management import calculate_sl


NAME = "fibZZ10"
ZIGZAG_DEVIATION = 10.0
TREND_ZIGZAG_DEVIATION = 30.0

INITIAL_CAPITAL = 100.0
RISK_PERCENT = 1.0
MAX_LEVERAGE = 5
COMMISSION_PER_SIDE = 0.00055
RISK_REWARD = 3.0

ENTRY_1_FIB = 0.805
TP_1_FIB = 0.500

ENTRY_2_FIB = 0.618
TP_2_GREEN_FIB = 0.382

ENABLE_ENTRY_3 = False
ENTRY_3_FIB = 0.382
TP_3_GREEN_FIB = 0.382

MAX_ENTRIES = 2




@dataclass
class LimitOrder:
    entry_number: int
    direction: str
    entry: float
    tp: float
    sl: float
    quantity: float
    position_value: float
    entry_fib: float
    tp_fib: float
    is_green_tp: bool
    status: str = "PENDING"


@dataclass
class StrategySignal:
    strategy: str
    direction: str
    zigzag_deviation: float
    swing_start: float
    swing_end: float
    swing_start_index: int
    swing_end_index: int
    confirmed_at: int
    fibonacci: dict[str, float]
    green_fibonacci: dict[str, float]
    orders: list[dict[str, Any]]
    cancelled_reason: str | None = None



def _normalize_candles(
    candles: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized = []

    for candle in candles:
        normalized.append({
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": float(candle.get("volume", 0.0)),
            "timestamp": candle.get(
                "timestamp",
                candle.get("time"),
            ),
        })

    return normalized



def calculate_green_fibonacci(
    swing_start: float,
    swing_end: float,
) -> dict[float, float]:
    swing_start = float(swing_start)
    swing_end = float(swing_end)

    if swing_start <= 0 or swing_end <= 0:
        raise ValueError("Свинг должен иметь положительные цены.")

    if swing_start == swing_end:
        raise ValueError("Амплитуда свинга не может быть нулевой.")

    direction = 1.0 if swing_end > swing_start else -1.0
    impulse_size = abs(swing_end - swing_start)

    fib_618 = fibo_price(
        swing_start,
        swing_end,
        0.618,
    )

    green_382 = fib_618 + direction * 0.382 * impulse_size
    green_618 = fib_618 + direction * 0.618 * impulse_size

    return {
        0.382: green_382,
        0.618: green_618,
    }



def calculate_position(
    capital: float,
    entry: float,
    sl: float,
) -> dict[str, float]:
    """Размер позиции от денежного риска, а не от процента маржи.

    Риск включает оценку комиссии входа и выхода. Плечо ограничивает
    номинал позиции, но не увеличивает допустимый убыток.
    """
    capital = float(capital)
    entry = float(entry)
    sl = float(sl)

    if capital <= 0 or entry <= 0 or sl <= 0 or entry == sl:
        raise ValueError("Капитал, Entry и SL должны быть корректными положительными ценами.")

    risk_budget = capital * RISK_PERCENT / 100.0
    stop_distance = abs(entry - sl)
    fee_per_unit = (entry + sl) * COMMISSION_PER_SIDE
    risk_per_unit = stop_distance + fee_per_unit
    if risk_per_unit <= 0:
        raise ValueError("Некорректный риск на единицу количества.")

    quantity = risk_budget / risk_per_unit
    max_notional = capital * MAX_LEVERAGE
    max_quantity = max_notional / entry
    quantity = min(quantity, max_quantity)
    position_value = quantity * entry

    return {
        "capital": capital,
        "risk_budget": risk_budget,
        "estimated_total_risk": quantity * risk_per_unit,
        "position_value": position_value,
        "quantity": quantity,
        "estimated_entry_fee": position_value * COMMISSION_PER_SIDE,
        "estimated_exit_fee": quantity * sl * COMMISSION_PER_SIDE,
        "max_leverage": float(MAX_LEVERAGE),
    }




def _make_order(
    entry_number: int,
    direction: str,
    entry_fib: float,
    tp: float,
    tp_fib: float,
    is_green_tp: bool,
    swing_start: float,
    swing_end: float,
    capital: float,
) -> dict[str, Any]:
    entry = fibo_price(
        swing_start,
        swing_end,
        entry_fib,
    )

    sl = calculate_sl(
        entry=entry,
        tp=tp,
        rr=RISK_REWARD,
        direction=direction,
    )

    position = calculate_position(
        capital=capital,
        entry=entry,
        sl=sl,
    )

    order = LimitOrder(
        entry_number=entry_number,
        direction=direction,
        entry=entry,
        tp=tp,
        sl=sl,
        quantity=position["quantity"],
        position_value=position["position_value"],
        entry_fib=entry_fib,
        tp_fib=tp_fib,
        is_green_tp=is_green_tp,
    )

    result = asdict(order)
    result["estimated_entry_fee"] = position["estimated_entry_fee"]
    result["rr"] = RISK_REWARD

    return result




def get_global_trend(
    candles: Sequence[dict[str, Any]],
    deviation: float,
) -> str | None:
    swings = find_confirmed_swings(
        candles,
        deviation_percent=deviation,
    )

    highs = [float(s.price) for s in swings if s.kind == "HIGH"]
    lows = [float(s.price) for s in swings if s.kind == "LOW"]

    if len(highs) < 2 or len(lows) < 2:
        return None

    if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
        return "LONG"

    if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
        return "SHORT"

    return None



def analyze(
    candles: Sequence[dict[str, Any]],
    current_price: float | None = None,
    capital: float = INITIAL_CAPITAL,
    open_positions: int = 0,
    pending_orders: int = 0,
) -> dict[str, Any] | None:

    candles = _normalize_candles(candles)

    if len(candles) < 2:
        return None

    if current_price is None:
        current_price = candles[-1]["close"]

    current_price = float(current_price)

    if current_price <= 0:
        raise ValueError("Текущая цена должна быть больше нуля.")

    if open_positions < 0 or pending_orders < 0:
        raise ValueError("Количество позиций и ордеров не может быть отрицательным.")

    swings = find_confirmed_swings(
        candles,
        deviation_percent=ZIGZAG_DEVIATION,
    )

    if len(swings) < 2:
        return None

    first_swing = swings[-2]
    last_swing = swings[-1]

    if first_swing.kind == last_swing.kind:
        return None

    if first_swing.kind == "LOW" and last_swing.kind == "HIGH":
        direction = "LONG"

    elif first_swing.kind == "HIGH" and last_swing.kind == "LOW":
        direction = "SHORT"

    else:
        return None

    global_trend = get_global_trend(candles, TREND_ZIGZAG_DEVIATION)

    if global_trend is None or global_trend != direction:
        return None

    swing_start = float(first_swing.price)
    swing_end = float(last_swing.price)

    # Do not trade an invalidated structure.
    if direction == "LONG" and current_price <= swing_start:
        return None

    if direction == "SHORT" and current_price >= swing_start:
        return None

    # Ordinary Fibonacci retracement levels.
    fibs = fibonacci_levels(
        swing_start,
        swing_end,
        levels=(0.382, 0.500, 0.618, 0.805),
    )

    # Trend-direction extension targets.
    green_fibs = calculate_green_fibonacci(
        swing_start,
        swing_end,
    )

    order_definitions = [
        {
            "entry_number": 1,
            "entry_fib": ENTRY_1_FIB,
            "tp": fibs[TP_1_FIB],
            "tp_fib": TP_1_FIB,
            "is_green_tp": False,
        },
        {
            "entry_number": 2,
            "entry_fib": ENTRY_2_FIB,
            "tp": green_fibs[TP_2_GREEN_FIB],
            "tp_fib": TP_2_GREEN_FIB,
            "is_green_tp": True,
        },
    ]

    if ENABLE_ENTRY_3:
        order_definitions.append({
            "entry_number": 3,
            "entry_fib": ENTRY_3_FIB,
            "tp": green_fibs[TP_3_GREEN_FIB],
            "tp_fib": TP_3_GREEN_FIB,
            "is_green_tp": True,
        })

    # Maximum two entries by default.
    remaining_slots = max(
        0,
        MAX_ENTRIES - open_positions - pending_orders,
    )

    orders: list[dict[str, Any]] = []

    for definition in order_definitions:
        if len(orders) >= remaining_slots:
            break

        entry = fibo_price(
            swing_start,
            swing_end,
            definition["entry_fib"],
        )

        # Do not place a limit order if price has already
        # reached or crossed the entry when the setup is checked.
        if direction == "LONG" and current_price <= entry:
            continue

        if direction == "SHORT" and current_price >= entry:
            continue

        try:
            order = _make_order(
                entry_number=definition["entry_number"],
                direction=direction,
                entry_fib=definition["entry_fib"],
                tp=definition["tp"],
                tp_fib=definition["tp_fib"],
                is_green_tp=definition["is_green_tp"],
                swing_start=swing_start,
                swing_end=swing_end,
                capital=capital,
            )
        except ValueError:
            continue

        orders.append(order)

    signal = StrategySignal(
        strategy=NAME,
        direction=direction,
        zigzag_deviation=ZIGZAG_DEVIATION,
        swing_start=swing_start,
        swing_end=swing_end,
        swing_start_index=first_swing.index,
        swing_end_index=last_swing.index,
        confirmed_at=last_swing.confirmed_at,
        fibonacci={
            str(level): price
            for level, price in fibs.items()
        },
        green_fibonacci={
            str(level): price
            for level, price in green_fibs.items()
        },
        orders=orders,
    )

    result = asdict(signal)

    result["current_price"] = current_price
    result["swing_start_timestamp"] = (
        candles[first_swing.index].get("timestamp")
        if 0 <= first_swing.index < len(candles) else None
    )
    result["swing_end_timestamp"] = (
        candles[last_swing.index].get("timestamp")
        if 0 <= last_swing.index < len(candles) else None
    )
    result["capital"] = float(capital)
    result["commission_per_side"] = COMMISSION_PER_SIDE
    result["initial_capital"] = INITIAL_CAPITAL
    result["risk_percent"] = RISK_PERCENT
    result["max_leverage"] = MAX_LEVERAGE
    result["rr"] = RISK_REWARD
    result["max_entries"] = MAX_ENTRIES
    result["entry_3_enabled"] = ENABLE_ENTRY_3
    result["global_trend"] = global_trend
    result["trend_zigzag_deviation"] = TREND_ZIGZAG_DEVIATION

    return result


# ============================================================
# TELEGRAM-FRIENDLY FORMAT
# ============================================================

def format_signal(signal: dict[str, Any] | None) -> str:
    if signal is None:
        return (
            "📐 fibZZ10\n\n"
            "Нет нового подходящего сетапа.\n"
            "Ожидается подтверждённая структура ZigZag."
        )

    direction = signal["direction"]
    orders = signal["orders"]

    lines = [
        "📐 MASB — fibZZ10",
        "",
        f"Направление: {direction}",
        f"ZigZag: {signal['zigzag_deviation']}%",
        f"RR: 1:{signal['rr']}",
        f"Текущая цена: {signal['current_price']:.8f}",
        "",
        "Fibonacci:",
    ]

    for level, price in signal["fibonacci"].items():
        lines.append(f"  {level}: {price:.8f}")

    lines.extend([
        "",
        "Green Fibonacci:",
    ])

    for level, price in signal["green_fibonacci"].items():
        lines.append(f"  {level}: {price:.8f}")

    lines.append("")

    if not orders:
        lines.append("Нет доступных лимитных входов.")
    else:
        for order in orders:
            tp_name = (
                f"Green Fib {order['tp_fib']}"
                if order["is_green_tp"]
                else f"Fib {order['tp_fib']}"
            )

            lines.extend([
                f"Сделка №{order['entry_number']}",
                f"  Тип: LIMIT {order['direction']}",
                f"  Entry Fib: {order['entry_fib']}",
                f"  Entry: {order['entry']:.8f}",
                f"  TP ({tp_name}): {order['tp']:.8f}",
                f"  SL: {order['sl']:.8f}",
                f"  RR: 1:{order['rr']}",
                f"  Номинал: {order['position_value']:.2f} USDT",
                f"  Количество: {order['quantity']:.8f}",
                f"  Комиссия входа (оценка): "
                f"{order['estimated_entry_fee']:.4f} USDT",
                "",
            ])

    return "\n".join(lines)


if __name__ == "__main__":
    print(f"Стратегия {NAME}")
    print(f"ZigZag: {ZIGZAG_DEVIATION}%")
    print(f"Начальный капитал: {INITIAL_CAPITAL} USDT")
    print(f"Риск на сделку: {RISK_PERCENT}%")
    print(f"Максимальное плечо: {MAX_LEVERAGE}x")
    print(f"RR: 1:{RISK_REWARD}")
    print(f"Комиссия за сторону: {COMMISSION_PER_SIDE * 100:.3f}%")
    print(f"Максимум входов: {MAX_ENTRIES}")
    print(f"Третий вход включён: {ENABLE_ENTRY_3}")
    print("Стратегия загружена. Для анализа передай свечи в analyze().")