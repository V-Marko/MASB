
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence




NAME = "obATR3"

TIMEFRAME = "1h"

INITIAL_CAPITAL = 100.0
RISK_PERCENT = 1.0
MAX_LEVERAGE = 5.0

COMMISSION_PER_SIDE = 0.00055
SLIPPAGE_PERCENT = 0.0

ATR_PERIOD = 14
ATR_SL_BUFFER = 0.10

SWING_LENGTH = 5
OB_LOOKBACK = 12
IMPULSE_ATR_MULTIPLIER = 1.0
MAX_OB_AGE = 100

RISK_REWARD = 3.0

USE_TREND_FILTER = False
EMA_FAST = 50
EMA_SLOW = 200

USE_LIQUIDITY_FILTER = True
MIN_ROOM_R = 3.0



@dataclass
class OrderBlock:
    direction: str
    high: float
    low: float
    candle_index: int
    confirmed_at: int
    used: bool = False


@dataclass
class TradeOrder:
    direction: str
    entry: float
    tp: float
    sl: float
    risk_per_unit: float
    quantity: float
    position_value: float
    risk_amount: float
    rr: float
    ob_high: float
    ob_low: float
    atr: float
    status: str = "PENDING"


@dataclass
class StrategySignal:
    strategy: str
    direction: str
    timeframe: str
    entry: float
    tp: float
    sl: float
    rr: float
    risk_percent: float
    quantity: float
    position_value: float
    ob_high: float
    ob_low: float
    ob_index: int
    confirmed_at: int
    atr: float
    orders: list[dict[str, Any]]
    cancelled_reason: str | None = None



def _normalize_candles(
    candles: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    for candle in candles:
        normalized = {
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": float(candle.get("volume", 0.0)),
            "timestamp": candle.get(
                "timestamp",
                candle.get("time"),
            ),
        }

        if (
            normalized["open"] <= 0
            or normalized["high"] <= 0
            or normalized["low"] <= 0
            or normalized["close"] <= 0
        ):
            raise ValueError("OHLC-цены должны быть положительными.")

        if normalized["high"] < max(
            normalized["open"],
            normalized["close"],
            normalized["low"],
        ):
            raise ValueError("Некорректная свеча: high ниже OHLC.")

        if normalized["low"] > min(
            normalized["open"],
            normalized["close"],
            normalized["high"],
        ):
            raise ValueError("Некорректная свеча: low выше OHLC.")

        result.append(normalized)

    return result



def calculate_atr(
    candles: Sequence[dict[str, Any]],
    period: int = ATR_PERIOD,
) -> list[float | None]:
    if period < 1:
        raise ValueError("ATR period должен быть положительным.")

    values: list[float | None] = [None] * len(candles)
    true_ranges: list[float] = []

    for i, candle in enumerate(candles):
        high = candle["high"]
        low = candle["low"]

        if i == 0:
            true_range = high - low
        else:
            previous_close = candles[i - 1]["close"]

            true_range = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )

        true_ranges.append(true_range)

        if i >= period - 1:
            values[i] = sum(
                true_ranges[i - period + 1:i + 1]
            ) / period

    return values



def calculate_ema(
    values: Sequence[float],
    period: int,
) -> list[float | None]:
    if period < 1:
        raise ValueError("EMA period должен быть положительным.")

    result: list[float | None] = [None] * len(values)

    if len(values) < period:
        return result

    initial = sum(values[:period]) / period
    result[period - 1] = initial

    multiplier = 2.0 / (period + 1)
    previous = initial

    for i in range(period, len(values)):
        previous = (
            values[i] * multiplier
            + previous * (1.0 - multiplier)
        )
        result[i] = previous

    return result



def find_confirmed_swings(
    candles: Sequence[dict[str, Any]],
    length: int = SWING_LENGTH,
) -> list[dict[str, Any]]:
    if length < 1:
        raise ValueError("Swing length должен быть положительным.")

    swings: list[dict[str, Any]] = []

    for i in range(length, len(candles) - length):
        high = candles[i]["high"]
        low = candles[i]["low"]

        left = candles[i - length:i]
        right = candles[i + 1:i + length + 1]

        is_high = (
            all(high > candle["high"] for candle in left)
            and all(high >= candle["high"] for candle in right)
        )

        is_low = (
            all(low < candle["low"] for candle in left)
            and all(low <= candle["low"] for candle in right)
        )

        confirmed_at = i + length

        if is_high:
            swings.append({
                "kind": "HIGH",
                "price": high,
                "index": i,
                "confirmed_at": confirmed_at,
            })

        if is_low:
            swings.append({
                "kind": "LOW",
                "price": low,
                "index": i,
                "confirmed_at": confirmed_at,
            })

    swings.sort(key=lambda item: item["index"])
    return swings



def find_order_blocks(
    candles: Sequence[dict[str, Any]],
    atr_values: Sequence[float | None],
) -> list[OrderBlock]:
    blocks: list[OrderBlock] = []

    swings = find_confirmed_swings(
        candles,
        length=SWING_LENGTH,
    )

    swing_highs = [
        item for item in swings if item["kind"] == "HIGH"
    ]

    swing_lows = [
        item for item in swings if item["kind"] == "LOW"
    ]

    last_high: dict[str, Any] | None = None
    last_low: dict[str, Any] | None = None

    swing_high_cursor = 0
    swing_low_cursor = 0

    for i in range(len(candles)):
        candle = candles[i]

        while (
            swing_high_cursor < len(swing_highs)
            and swing_highs[swing_high_cursor]["confirmed_at"] <= i
        ):
            last_high = swing_highs[swing_high_cursor]
            swing_high_cursor += 1

        while (
            swing_low_cursor < len(swing_lows)
            and swing_lows[swing_low_cursor]["confirmed_at"] <= i
        ):
            last_low = swing_lows[swing_low_cursor]
            swing_low_cursor += 1

        atr = atr_values[i]

        if atr is None or atr <= 0:
            continue

        bullish_body = candle["close"] - candle["open"]
        bearish_body = candle["open"] - candle["close"]

        bullish_break = (
            last_high is not None
            and candle["close"] > last_high["price"]
            and (
                i == 0
                or candles[i - 1]["close"] <= last_high["price"]
            )
        )

        bearish_break = (
            last_low is not None
            and candle["close"] < last_low["price"]
            and (
                i == 0
                or candles[i - 1]["close"] >= last_low["price"]
            )
        )

        if (
            bullish_break
            and bullish_body >= atr * IMPULSE_ATR_MULTIPLIER
        ):
            for j in range(i - 1, max(-1, i - OB_LOOKBACK - 1), -1):
                if candles[j]["close"] < candles[j]["open"]:
                    blocks.append(OrderBlock(
                        direction="LONG",
                        high=candles[j]["high"],
                        low=candles[j]["low"],
                        candle_index=j,
                        confirmed_at=i,
                    ))
                    break

        if (
            bearish_break
            and bearish_body >= atr * IMPULSE_ATR_MULTIPLIER
        ):
            for j in range(i - 1, max(-1, i - OB_LOOKBACK - 1), -1):
                if candles[j]["close"] > candles[j]["open"]:
                    blocks.append(OrderBlock(
                        direction="SHORT",
                        high=candles[j]["high"],
                        low=candles[j]["low"],
                        candle_index=j,
                        confirmed_at=i,
                    ))
                    break

    return blocks

def calculate_position(
    capital: float,
    entry: float,
    sl: float,
    direction: str,
) -> dict[str, float]:
    capital = float(capital)
    entry = float(entry)
    sl = float(sl)

    if capital <= 0:
        raise ValueError("Капитал должен быть больше нуля.")

    if entry <= 0 or sl <= 0:
        raise ValueError("Entry и SL должны быть положительными.")

    if direction == "LONG":
        risk_per_unit = entry - sl
    elif direction == "SHORT":
        risk_per_unit = sl - entry
    else:
        raise ValueError("Direction должен быть LONG или SHORT.")

    if risk_per_unit <= 0:
        raise ValueError("SL находится на неправильной стороне Entry.")

    risk_amount = capital * RISK_PERCENT / 100.0
    quantity_by_risk = risk_amount / risk_per_unit

    max_position_value = capital * MAX_LEVERAGE
    quantity_by_leverage = max_position_value / entry

    quantity = min(quantity_by_risk, quantity_by_leverage)
    position_value = quantity * entry

    return {
        "capital": capital,
        "risk_per_unit": risk_per_unit,
        "risk_amount": quantity * risk_per_unit,
        "quantity": quantity,
        "position_value": position_value,
        "estimated_entry_fee": position_value * COMMISSION_PER_SIDE,
    }



def _make_order(
    direction: str,
    entry: float,
    ob: OrderBlock,
    atr: float,
    capital: float,
) -> dict[str, Any]:
    entry = float(entry)
    atr = float(atr)

    if entry <= 0 or atr <= 0:
        raise ValueError("Entry и ATR должны быть положительными.")

    buffer = atr * ATR_SL_BUFFER

    if direction == "LONG":
        sl = ob.low - buffer

        if not 0 < sl < entry:
            raise ValueError("Некорректный LONG SL.")

        risk = entry - sl
        tp = entry + risk * RISK_REWARD

        actual_rr = (tp - entry) / risk

    elif direction == "SHORT":
        sl = ob.high + buffer

        if not sl > entry:
            raise ValueError("Некорректный SHORT SL.")

        risk = sl - entry
        tp = entry - risk * RISK_REWARD

        if tp <= 0:
            raise ValueError("SHORT TP должен быть положительным.")

        actual_rr = (entry - tp) / risk

    else:
        raise ValueError("Неизвестное направление.")

    if risk <= 0:
        raise ValueError("Риск сделки должен быть положительным.")

    position = calculate_position(
        capital=capital,
        entry=entry,
        sl=sl,
        direction=direction,
    )

    order = TradeOrder(
        direction=direction,
        entry=entry,
        tp=tp,
        sl=sl,
        risk_per_unit=risk,
        quantity=position["quantity"],
        position_value=position["position_value"],
        risk_amount=position["risk_amount"],
        rr=actual_rr,
        ob_high=ob.high,
        ob_low=ob.low,
        atr=atr,
    )

    result = asdict(order)
    result["estimated_entry_fee"] = position["estimated_entry_fee"]
    result["estimated_exit_fee_at_tp"] = (
        position["quantity"] * tp * COMMISSION_PER_SIDE
    )

    return result



def _liquidity_allows(
    direction: str,
    entry: float,
    sl: float,
    last_swing_high: float | None,
    last_swing_low: float | None,
) -> bool:
    if not USE_LIQUIDITY_FILTER:
        return True

    risk = abs(entry - sl)

    if risk <= 0:
        return False

    required_room = risk * MIN_ROOM_R

    if direction == "LONG" and last_swing_high is not None:
        if entry < last_swing_high < entry + required_room:
            return False

    if direction == "SHORT" and last_swing_low is not None:
        if entry - required_room < last_swing_low < entry:
            return False

    return True



def analyze(
    candles: Sequence[dict[str, Any]],
    current_price: float | None = None,
    capital: float = INITIAL_CAPITAL,
    open_positions: int = 0,
    pending_orders: int = 0,
) -> dict[str, Any] | None:
    normalized = _normalize_candles(candles)

    if len(normalized) < max(
        ATR_PERIOD + 1,
        SWING_LENGTH * 2 + 2,
    ):
        return None

    if open_positions < 0 or pending_orders < 0:
        raise ValueError(
            "Количество позиций и ордеров не может быть отрицательным."
        )

    if current_price is None:
        current_price = normalized[-1]["close"]

    current_price = float(current_price)

    if current_price <= 0:
        raise ValueError("Текущая цена должна быть положительной.")

    atr_values = calculate_atr(normalized)

    atr = atr_values[-1]

    if atr is None or atr <= 0:
        return None

    closes = [candle["close"] for candle in normalized]

    ema_fast = calculate_ema(closes, EMA_FAST)
    ema_slow = calculate_ema(closes, EMA_SLOW)

    if USE_TREND_FILTER:
        if ema_fast[-1] is None or ema_slow[-1] is None:
            return None

    swings = find_confirmed_swings(
        normalized,
        length=SWING_LENGTH,
    )

    last_swing_high = next(
        (
            float(item["price"])
            for item in reversed(swings)
            if item["kind"] == "HIGH"
            and item["confirmed_at"] < len(normalized) - 1
        ),
        None,
    )

    last_swing_low = next(
        (
            float(item["price"])
            for item in reversed(swings)
            if item["kind"] == "LOW"
            and item["confirmed_at"] < len(normalized) - 1
        ),
        None,
    )

    blocks = find_order_blocks(
        normalized,
        atr_values,
    )

    if not blocks:
        return None

    candle_index = len(normalized) - 1
    candle = normalized[-1]

    if current_price != candle["close"]:

        return None

    long_candidates = [
        block for block in blocks
        if block.direction == "LONG"
        and block.confirmed_at < candle_index
        and candle_index - block.candle_index <= MAX_OB_AGE
        and candle["low"] <= block.high
        and candle["close"] > block.high
        and candle["close"] > candle["open"]
        and candle["close"] >= block.low
    ]

    short_candidates = [
        block for block in blocks
        if block.direction == "SHORT"
        and block.confirmed_at < candle_index
        and candle_index - block.candle_index <= MAX_OB_AGE
        and candle["high"] >= block.low
        and candle["close"] < block.low
        and candle["close"] < candle["open"]
        and candle["close"] <= block.high
    ]

    candidates: list[OrderBlock] = []

    if long_candidates:
        candidates.append(long_candidates[-1])

    if short_candidates:
        candidates.append(short_candidates[-1])

    if not candidates:
        return None

    if len(candidates) > 1:
        return None

    ob = candidates[0]
    direction = ob.direction

    if USE_TREND_FILTER:
        fast = ema_fast[-1]
        slow = ema_slow[-1]

        if direction == "LONG" and not fast > slow:
            return None

        if direction == "SHORT" and not fast < slow:
            return None

    buffer = atr * ATR_SL_BUFFER

    if direction == "LONG":
        sl_preview = ob.low - buffer
    else:
        sl_preview = ob.high + buffer

    if not _liquidity_allows(
        direction=direction,
        entry=current_price,
        sl=sl_preview,
        last_swing_high=last_swing_high,
        last_swing_low=last_swing_low,
    ):
        return None


    if open_positions + pending_orders > 0:
        return None

    try:
        order = _make_order(
            direction=direction,
            entry=current_price,
            ob=ob,
            atr=atr,
            capital=capital,
        )
    except ValueError:
        return None

    signal = StrategySignal(
        strategy=NAME,
        direction=direction,
        timeframe=TIMEFRAME,
        entry=order["entry"],
        tp=order["tp"],
        sl=order["sl"],
        rr=order["rr"],
        risk_percent=RISK_PERCENT,
        quantity=order["quantity"],
        position_value=order["position_value"],
        ob_high=ob.high,
        ob_low=ob.low,
        ob_index=ob.candle_index,
        confirmed_at=ob.confirmed_at,
        atr=atr,
        orders=[order],
    )

    result = asdict(signal)
    result["current_price"] = current_price
    result["capital"] = float(capital)
    result["commission_per_side"] = COMMISSION_PER_SIDE
    result["slippage_percent"] = SLIPPAGE_PERCENT
    result["initial_capital"] = INITIAL_CAPITAL
    result["max_leverage"] = MAX_LEVERAGE
    result["atr_period"] = ATR_PERIOD
    result["atr_sl_buffer"] = ATR_SL_BUFFER
    result["swing_length"] = SWING_LENGTH
    result["risk_reward"] = RISK_REWARD

    return result




def format_signal(
    signal: dict[str, Any] | None,
) -> str:
    if signal is None:
        return (
            "📦 MASB — obATR3\n\n"
            "Нет нового подходящего Order Block сетапа.\n"
            "Ожидается подтверждённый импульс и возврат в зону."
        )

    direction = signal["direction"]
    order = signal["orders"][0]

    return "\n".join([
        "📦 MASB — obATR3",
        "",
        f"Таймфрейм: {signal['timeframe']}",
        f"Направление: {direction}",
        f"Entry: {order['entry']:.8f}",
        f"Take Profit: {order['tp']:.8f}",
        f"Stop Loss: {order['sl']:.8f}",
        f"RR: 1:{order['rr']:.2f}",
        "",
        "Order Block:",
        f"  High: {order['ob_high']:.8f}",
        f"  Low: {order['ob_low']:.8f}",
        f"  ATR: {order['atr']:.8f}",
        "",
        "Риск и размер позиции:",
        f"  Риск: {order['risk_amount']:.4f} USDT",
        f"  Количество: {order['quantity']:.8f}",
        f"  Номинал: {order['position_value']:.2f} USDT",
        f"  Комиссия входа (оценка): "
        f"{order['estimated_entry_fee']:.4f} USDT",
        f"  Комиссия выхода по TP (оценка): "
        f"{order['estimated_exit_fee_at_tp']:.4f} USDT",
        "",
        "Это сигнал стратегии, а не подтверждение исполнения ордера.",
    ])


if __name__ == "__main__":
    print(f"Стратегия: {NAME}")
    print(f"Таймфрейм: {TIMEFRAME}")
    print(f"Начальный капитал: {INITIAL_CAPITAL:.2f} USDT")
    print(f"Риск на сделку: {RISK_PERCENT:.2f}%")
    print(f"Максимальное плечо: {MAX_LEVERAGE:.1f}x")
    print(f"ATR buffer: {ATR_SL_BUFFER} × ATR({ATR_PERIOD})")
    print(f"RR: 1:{RISK_REWARD:.1f}")
    print("Стратегия загружена. Передай закрытые свечи в analyze().")
