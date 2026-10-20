from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence


# ============================================================
# MASB Trendline Breakout + Retest LONG/SHORT
# Interface modeled after the user's obATR3 strategy module.
# Feed CLOSED 1H candles in chronological order.
# This module generates signals; it does not execute exchange orders.
# ============================================================

NAME = "trendlineRR3"
TIMEFRAME = "1h"

INITIAL_CAPITAL = 100.0
RISK_PERCENT = 1.0
MAX_LEVERAGE = 5.0

COMMISSION_PER_SIDE = 0.00055
SLIPPAGE_PERCENT = 0.0

ATR_PERIOD = 14
ATR_SL_BUFFER = 0.20

PIVOT_LENGTH = 3
RETEST_MAX_BARS = 12
RETEST_ATR_TOLERANCE = 0.15
STOP_LOOKBACK = 5
MAX_SL_DISTANCE_ATR = 2.0

RISK_REWARD = 3.0

USE_TREND_FILTER = False
EMA_FAST = 50
EMA_SLOW = 200

USE_LIQUIDITY_FILTER = True
MIN_ROOM_R = 3.0


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
    trendline_level: float
    atr: float
    status: str = "SIGNAL"


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
    trendline_level: float
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
            "timestamp": candle.get("timestamp", candle.get("time")),
        }

        if any(
            normalized[key] <= 0
            for key in ("open", "high", "low", "close")
        ):
            raise ValueError("OHLC prices must be positive.")

        if normalized["high"] < max(
            normalized["open"], normalized["close"], normalized["low"]
        ):
            raise ValueError("Invalid candle: high is below an OHLC value.")

        if normalized["low"] > min(
            normalized["open"], normalized["close"], normalized["high"]
        ):
            raise ValueError("Invalid candle: low is above an OHLC value.")

        result.append(normalized)

    return result


def calculate_atr(
    candles: Sequence[dict[str, Any]],
    period: int = ATR_PERIOD,
) -> list[float | None]:
    """Wilder ATR, initialized with an SMA of the first period TR values."""
    if period < 1:
        raise ValueError("ATR period must be >= 1.")

    tr: list[float] = []
    result: list[float | None] = [None] * len(candles)

    for i, candle in enumerate(candles):
        if i == 0:
            value = candle["high"] - candle["low"]
        else:
            prev_close = candles[i - 1]["close"]
            value = max(
                candle["high"] - candle["low"],
                abs(candle["high"] - prev_close),
                abs(candle["low"] - prev_close),
            )
        tr.append(value)

        if i == period - 1:
            result[i] = sum(tr[:period]) / period
        elif i >= period:
            previous_atr = result[i - 1]
            if previous_atr is not None:
                result[i] = (previous_atr * (period - 1) + value) / period

    return result


def calculate_ema(values: Sequence[float], period: int) -> list[float | None]:
    if period < 1:
        raise ValueError("EMA period must be >= 1.")

    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result

    previous = sum(values[:period]) / period
    result[period - 1] = previous
    multiplier = 2.0 / (period + 1)

    for i in range(period, len(values)):
        previous = values[i] * multiplier + previous * (1.0 - multiplier)
        result[i] = previous

    return result


def find_confirmed_swings(
    candles: Sequence[dict[str, Any]],
    length: int = PIVOT_LENGTH,
) -> list[dict[str, Any]]:
    """Return swing highs/lows with the index when each became confirmed."""
    if length < 1:
        raise ValueError("Pivot length must be >= 1.")

    swings: list[dict[str, Any]] = []

    # A swing at index i is only knowable at i + length.
    for i in range(length, len(candles) - length):
        left = candles[i - length:i]
        right = candles[i + 1:i + length + 1]
        high = candles[i]["high"]
        low = candles[i]["low"]

        is_high = (
            all(high > item["high"] for item in left)
            and all(high >= item["high"] for item in right)
        )
        is_low = (
            all(low < item["low"] for item in left)
            and all(low <= item["low"] for item in right)
        )

        if is_high:
            swings.append({
                "kind": "HIGH",
                "price": high,
                "index": i,
                "confirmed_at": i + length,
            })
        if is_low:
            swings.append({
                "kind": "LOW",
                "price": low,
                "index": i,
                "confirmed_at": i + length,
            })

    swings.sort(key=lambda item: item["index"])
    return swings


def calculate_position(
    capital: float,
    entry: float,
    sl: float,
    direction: str,
) -> dict[str, float]:
    if capital <= 0:
        raise ValueError("Capital must be positive.")
    if entry <= 0 or sl <= 0:
        raise ValueError("Entry and SL must be positive.")

    if direction == "LONG":
        risk_per_unit = entry - sl
    elif direction == "SHORT":
        risk_per_unit = sl - entry
    else:
        raise ValueError("Direction must be LONG or SHORT.")

    if risk_per_unit <= 0:
        raise ValueError("Stop loss is on the wrong side of entry.")

    planned_risk_amount = capital * RISK_PERCENT / 100.0
    quantity_by_risk = planned_risk_amount / risk_per_unit
    quantity_by_leverage = capital * MAX_LEVERAGE / entry
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
    stop_anchor: float,
    trendline_level: float,
    atr: float,
    capital: float,
) -> dict[str, Any]:
    if entry <= 0 or atr <= 0:
        raise ValueError("Entry and ATR must be positive.")

    buffer = atr * ATR_SL_BUFFER

    if direction == "LONG":
        sl = stop_anchor - buffer
        risk = entry - sl
        tp = entry + risk * RISK_REWARD
    elif direction == "SHORT":
        sl = stop_anchor + buffer
        risk = sl - entry
        tp = entry - risk * RISK_REWARD
        if tp <= 0:
            raise ValueError("Calculated SHORT TP must be positive.")
    else:
        raise ValueError("Unknown direction.")

    if risk <= 0:
        raise ValueError("Trade risk must be positive.")

    position = calculate_position(capital, entry, sl, direction)
    order = TradeOrder(
        direction=direction,
        entry=entry,
        tp=tp,
        sl=sl,
        risk_per_unit=risk,
        quantity=position["quantity"],
        position_value=position["position_value"],
        risk_amount=position["risk_amount"],
        rr=RISK_REWARD,
        trendline_level=trendline_level,
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
    """
    Analyze a chronological sequence of CLOSED 1H candles.

    Returns a signal only when the latest candle confirms a retest.
    The caller should prevent duplicate processing of the same candle.
    """
    normalized = _normalize_candles(candles)

    if len(normalized) < max(
        ATR_PERIOD + 2,
        PIVOT_LENGTH * 2 + 3,
        EMA_SLOW if USE_TREND_FILTER else 0,
    ):
        return None

    if open_positions < 0 or pending_orders < 0:
        raise ValueError("Position/order counts cannot be negative.")
    if open_positions + pending_orders > 0:
        return None

    if current_price is None:
        current_price = normalized[-1]["close"]
    current_price = float(current_price)

    if current_price <= 0:
        raise ValueError("Current price must be positive.")

    # Only evaluate a closed candle at its close. This also keeps the
    # historical simulation and signal interface consistent.
    if current_price != normalized[-1]["close"]:
        return None

    atr_values = calculate_atr(normalized)
    atr = atr_values[-1]
    if atr is None or atr <= 0:
        return None

    closes = [c["close"] for c in normalized]
    ema_fast = calculate_ema(closes, EMA_FAST)
    ema_slow = calculate_ema(closes, EMA_SLOW)

    if USE_TREND_FILTER and (
        ema_fast[-1] is None or ema_slow[-1] is None
    ):
        return None

    swings = find_confirmed_swings(normalized, PIVOT_LENGTH)
    last_index = len(normalized) - 1
    last_candle = normalized[-1]
    previous_close = normalized[-2]["close"]

    # Only pivots already confirmed before the current signal candle
    # may be used to form the line.
    known_swings = [
        swing for swing in swings
        if swing["confirmed_at"] < last_index
    ]

    known_highs = [s for s in known_swings if s["kind"] == "HIGH"]
    known_lows = [s for s in known_swings if s["kind"] == "LOW"]

    if len(known_highs) < 2 and len(known_lows) < 2:
        return None

    last_swing_high = known_highs[-1]["price"] if known_highs else None
    last_swing_low = known_lows[-1]["price"] if known_lows else None

    # Build the historical trendline state one candle at a time.
    # This avoids using pivots before their confirmation time.
    wait_long = False
    wait_short = False
    long_level: float | None = None
    short_level: float | None = None
    long_break_bar: int | None = None
    short_break_bar: int | None = None
    latest_signal: dict[str, Any] | None = None

    confirmed_highs: list[dict[str, Any]] = []
    confirmed_lows: list[dict[str, Any]] = []
    swing_cursor = 0
    ordered_swings = sorted(swings, key=lambda s: s["confirmed_at"])

    for i in range(len(normalized)):
        while (
            swing_cursor < len(ordered_swings)
            and ordered_swings[swing_cursor]["confirmed_at"] <= i
        ):
            swing = ordered_swings[swing_cursor]
            if swing["kind"] == "HIGH":
                confirmed_highs.append(swing)
            else:
                confirmed_lows.append(swing)
            swing_cursor += 1

        if i == 0:
            continue

        candle = normalized[i]
        prev = normalized[i - 1]
        this_atr = atr_values[i]
        if this_atr is None or this_atr <= 0:
            continue

        resistance = resistance_prev = None
        support = support_prev = None

        if len(confirmed_highs) >= 2:
            p1, p2 = confirmed_highs[-2:]
            if p2["index"] > p1["index"] and p2["price"] < p1["price"]:
                slope = (
                    p2["price"] - p1["price"]
                ) / (p2["index"] - p1["index"])
                resistance = p2["price"] + slope * (i - p2["index"])
                resistance_prev = p2["price"] + slope * (i - 1 - p2["index"])

        if len(confirmed_lows) >= 2:
            p1, p2 = confirmed_lows[-2:]
            if p2["index"] > p1["index"] and p2["price"] > p1["price"]:
                slope = (
                    p2["price"] - p1["price"]
                ) / (p2["index"] - p1["index"])
                support = p2["price"] + slope * (i - p2["index"])
                support_prev = p2["price"] + slope * (i - 1 - p2["index"])

        close = candle["close"]
        open_ = candle["open"]
        high = candle["high"]
        low = candle["low"]

        break_long = (
            resistance is not None
            and resistance_prev is not None
            and close > resistance
            and prev["close"] <= resistance_prev
        )
        break_short = (
            support is not None
            and support_prev is not None
            and close < support
            and prev["close"] >= support_prev
        )

        if break_long:
            wait_long = True
            wait_short = False
            long_level = resistance
            long_break_bar = i

        if break_short:
            wait_short = True
            wait_long = False
            short_level = support
            short_break_bar = i

        if (
            wait_long
            and long_break_bar is not None
            and i - long_break_bar > RETEST_MAX_BARS
        ):
            wait_long = False

        if (
            wait_short
            and short_break_bar is not None
            and i - short_break_bar > RETEST_MAX_BARS
        ):
            wait_short = False

        long_retest = (
            wait_long
            and long_break_bar is not None
            and i > long_break_bar
            and long_level is not None
            and low <= long_level + this_atr * RETEST_ATR_TOLERANCE
            and close > long_level
            and close > open_
        )
        short_retest = (
            wait_short
            and short_break_bar is not None
            and i > short_break_bar
            and short_level is not None
            and high >= short_level - this_atr * RETEST_ATR_TOLERANCE
            and close < short_level
            and close < open_
        )

        # Signals on older candles are only used to advance the state.
        # We return only a valid signal on the latest candle.
        if i != last_index:
            if long_retest:
                wait_long = False
                wait_short = False
            elif short_retest:
                wait_short = False
                wait_long = False
            continue

        direction = None
        line_level = None
        stop_anchor = None

        if long_retest:
            direction = "LONG"
            line_level = long_level
            start = max(0, i - STOP_LOOKBACK + 1)
            stop_anchor = min(c["low"] for c in normalized[start:i + 1])
            wait_long = False
            wait_short = False

        elif short_retest:
            direction = "SHORT"
            line_level = short_level
            start = max(0, i - STOP_LOOKBACK + 1)
            stop_anchor = max(c["high"] for c in normalized[start:i + 1])
            wait_short = False
            wait_long = False

        if direction is None or line_level is None or stop_anchor is None:
            continue

        if USE_TREND_FILTER:
            fast = ema_fast[-1]
            slow = ema_slow[-1]
            if direction == "LONG" and not fast > slow:
                return None
            if direction == "SHORT" and not fast < slow:
                return None

        sl_preview = (
            stop_anchor - this_atr * ATR_SL_BUFFER
            if direction == "LONG"
            else stop_anchor + this_atr * ATR_SL_BUFFER
        )
        risk_preview = abs(close - sl_preview)

        if risk_preview <= 0 or risk_preview / this_atr > MAX_SL_DISTANCE_ATR:
            return None

        if not _liquidity_allows(
            direction=direction,
            entry=close,
            sl=sl_preview,
            last_swing_high=last_swing_high,
            last_swing_low=last_swing_low,
        ):
            return None

        try:
            order = _make_order(
                direction=direction,
                entry=close,
                stop_anchor=stop_anchor,
                trendline_level=line_level,
                atr=this_atr,
                capital=capital,
            )
        except ValueError:
            return None

        # Ensure the actual risk gate is applied to the final stop.
        if abs(order["entry"] - order["sl"]) / this_atr > MAX_SL_DISTANCE_ATR:
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
            trendline_level=line_level,
            confirmed_at=i,
            atr=this_atr,
            orders=[order],
        )

        latest_signal = asdict(signal)
        latest_signal.update({
            "current_price": current_price,
            "capital": float(capital),
            "commission_per_side": COMMISSION_PER_SIDE,
            "slippage_percent": SLIPPAGE_PERCENT,
            "initial_capital": INITIAL_CAPITAL,
            "max_leverage": MAX_LEVERAGE,
            "atr_period": ATR_PERIOD,
            "atr_sl_buffer": ATR_SL_BUFFER,
            "pivot_length": PIVOT_LENGTH,
            "risk_reward": RISK_REWARD,
        })

    return latest_signal


def format_signal(signal: dict[str, Any] | None) -> str:
    if signal is None:
        return (
            "📈 MASB — trendlineRR3\n\n"
            "Нет нового подтверждённого сетапа.\n"
            "Ожидается пробой трендовой линии и ретест."
        )

    order = signal["orders"][0]
    return "\n".join([
        "📈 MASB — trendlineRR3",
        "",
        f"Таймфрейм: {signal['timeframe']}",
        f"Направление: {signal['direction']}",
        f"Entry: {order['entry']:.8f}",
        f"Take Profit: {order['tp']:.8f}",
        f"Stop Loss: {order['sl']:.8f}",
        f"RR: 1:{order['rr']:.2f}",
        "",
        "Трендовая линия:",
        f"  Уровень пробоя/ретеста: {order['trendline_level']:.8f}",
        f"  ATR: {order['atr']:.8f}",
        "",
        "Риск и размер позиции:",
        f"  Риск: {order['risk_amount']:.4f} USDT",
        f"  Количество: {order['quantity']:.8f}",
        f"  Номинал: {order['position_value']:.2f} USDT",
        f"  Комиссия входа (оценка): {order['estimated_entry_fee']:.4f} USDT",
        f"  Комиссия выхода по TP (оценка): {order['estimated_exit_fee_at_tp']:.4f} USDT",
        "",
        "Это сигнал стратегии, а не подтверждение исполнения ордера.",
    ])


if __name__ == "__main__":
    print(f"Стратегия: {NAME}")
    print(f"Таймфрейм: {TIMEFRAME}")
    print(f"Начальный капитал: {INITIAL_CAPITAL:.2f} USDT")
    print(f"Риск на сделку: {RISK_PERCENT:.2f}%")
    print(f"Максимальное плечо: {MAX_LEVERAGE:.1f}x")
    print(f"SL buffer: {ATR_SL_BUFFER} × ATR({ATR_PERIOD})")
    print(f"RR: 1:{RISK_REWARD:.1f}")
    print("Модуль загружен. Передай закрытые свечи в analyze().")
