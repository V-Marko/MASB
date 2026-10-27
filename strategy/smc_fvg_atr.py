from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence
from math import isfinite


# ============================================================
# STRATEGY: SMC + FVG + TREND FILTER
# ============================================================

NAME = "smcFVG1H"
TIMEFRAME = "1h"

INITIAL_CAPITAL = 100.0
RISK_PERCENT = 1.0
COMMISSION_PER_SIDE = 0.00055
RISK_REWARD = 3.0
MAX_LEVERAGE = 5.0

EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200
ATR_PERIOD = 14

SWING_LOOKBACK = 12
SWEEP_VALID_BARS = 5
SL_ATR_BUFFER = 0.10
MIN_FVG_ATR = 0.05

MAX_ENTRIES = 1


# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class StrategyOrder:
    entry_number: int
    direction: str
    order_type: str
    entry: float
    tp: float
    sl: float
    quantity: float
    position_value: float
    risk_usdt: float
    rr: float
    status: str = "PENDING"


@dataclass
class StrategySignal:
    strategy: str
    direction: str
    timeframe: str
    current_price: float
    trend: str
    atr: float
    sweep_type: str
    bos_type: str
    fvg_low: float
    fvg_high: float
    orders: list[dict[str, Any]]
    confirmed_at: Any = None
    cancelled_reason: str | None = None


# ============================================================
# CANDLE NORMALIZATION
# ============================================================

def _normalize_candles(
    candles: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []

    for candle in candles:
        normalized.append({
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": float(candle.get("volume", 0.0)),
            "timestamp": candle.get("timestamp", candle.get("time")),
        })

    return normalized


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []

    alpha = 2.0 / (period + 1.0)
    result: list[float] = []
    current = values[0]

    for value in values:
        current = alpha * value + (1.0 - alpha) * current
        result.append(current)

    return result


def _atr(candles: list[dict[str, Any]], period: int) -> list[float]:
    true_ranges: list[float] = []

    for index, candle in enumerate(candles):
        high = candle["high"]
        low = candle["low"]

        if index == 0:
            tr = high - low
        else:
            previous_close = candles[index - 1]["close"]
            tr = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )

        true_ranges.append(tr)

    # Wilder-style smoothing; the first ATR values are approximate warm-up values.
    return _ema(true_ranges, period)


# ============================================================
# TREND FILTER
# ============================================================

def _get_trend(candles: list[dict[str, Any]]) -> tuple[str, dict[str, float]]:
    closes = [candle["close"] for candle in candles]

    ema_fast = _ema(closes, EMA_FAST)[-1]
    ema_mid = _ema(closes, EMA_MID)[-1]
    ema_slow = _ema(closes, EMA_SLOW)[-1]
    current_close = closes[-1]

    if ema_fast > ema_mid and current_close > ema_slow:
        trend = "BULLISH"
    elif ema_fast < ema_mid and current_close < ema_slow:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    return trend, {
        "ema_fast": ema_fast,
        "ema_mid": ema_mid,
        "ema_slow": ema_slow,
    }


# ============================================================
# LIQUIDITY SWEEP + BOS + FVG
# ============================================================

def _analyze_structure(
    candles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Analyse only completed candles supplied by the caller.

    Sweep and BOS use prior-candle ranges. FVG is the classic three-candle
    imbalance. No centered windows or future candles are accessed.
    """
    minimum = max(EMA_SLOW, SWING_LOOKBACK + SWEEP_VALID_BARS + 3, ATR_PERIOD + 3)
    if len(candles) < minimum:
        return None

    last_index = len(candles) - 1
    current = candles[last_index]
    previous_two = candles[last_index - 2]

    # Range before the current candle; excludes the current candle itself.
    prior_window = candles[last_index - SWING_LOOKBACK:last_index]
    prior_high = max(candle["high"] for candle in prior_window)
    prior_low = min(candle["low"] for candle in prior_window)

    # Current candle's liquidity sweep and structure break.
    bull_sweep_now = current["low"] < prior_low and current["close"] > prior_low
    bear_sweep_now = current["high"] > prior_high and current["close"] < prior_high

    bull_bos_now = current["close"] > prior_high
    bear_bos_now = current["close"] < prior_low

    # Find recent confirmed sweeps in the previous N closed candles.
    recent_start = max(0, last_index - SWEEP_VALID_BARS + 1)
    recent_candles = candles[recent_start:last_index + 1]
    recent_bull_sweeps: list[tuple[int, float]] = []
    recent_bear_sweeps: list[tuple[int, float]] = []

    for index in range(recent_start, last_index + 1):
        left = max(0, index - SWING_LOOKBACK)
        previous = candles[left:index]
        if len(previous) < SWING_LOOKBACK:
            continue

        prior_hi = max(c["high"] for c in previous)
        prior_lo = min(c["low"] for c in previous)
        candle = candles[index]

        if candle["low"] < prior_lo and candle["close"] > prior_lo:
            recent_bull_sweeps.append((index, candle["low"]))
        if candle["high"] > prior_hi and candle["close"] < prior_hi:
            recent_bear_sweeps.append((index, candle["high"]))

    # Three-candle FVG on the latest closed candle.
    bull_fvg_low = previous_two["high"]
    bull_fvg_high = current["low"]
    bear_fvg_low = current["high"]
    bear_fvg_high = previous_two["low"]

    atr_value = _atr(candles, ATR_PERIOD)[-1]
    if not isfinite(atr_value) or atr_value <= 0:
        return None

    bull_fvg_size = bull_fvg_high - bull_fvg_low
    bear_fvg_size = bear_fvg_high - bear_fvg_low

    bull_fvg = (
        bull_fvg_size > 0
        and bull_fvg_size >= atr_value * MIN_FVG_ATR
        and current["close"] > current["open"]
    )
    bear_fvg = (
        bear_fvg_size > 0
        and bear_fvg_size >= atr_value * MIN_FVG_ATR
        and current["close"] < current["open"]
    )

    trend, emas = _get_trend(candles)

    # BOS can happen on the current candle or within the recent confirmation window.
    # A sweep and BOS must both be present in the recent closed-candle window.
    recent_bull_bos = False
    recent_bear_bos = False

    for index in range(recent_start, last_index + 1):
        left = max(0, index - SWING_LOOKBACK)
        previous = candles[left:index]
        if len(previous) < SWING_LOOKBACK:
            continue
        prior_hi = max(c["high"] for c in previous)
        prior_lo = min(c["low"] for c in previous)
        candle = candles[index]
        recent_bull_bos = recent_bull_bos or candle["close"] > prior_hi
        recent_bear_bos = recent_bear_bos or candle["close"] < prior_lo

    # Current FVG + recent sweep + recent BOS + directional trend.
    long_valid = (
        bull_fvg
        and bool(recent_bull_sweeps)
        and recent_bull_bos
        and trend == "BULLISH"
    )
    short_valid = (
        bear_fvg
        and bool(recent_bear_sweeps)
        and recent_bear_bos
        and trend == "BEARISH"
    )

    if long_valid:
        sweep_index, sweep_extreme = recent_bull_sweeps[-1]
        return {
            "direction": "LONG",
            "trend": trend,
            "atr": atr_value,
            "sweep_type": "BULLISH LIQUIDITY SWEEP",
            "bos_type": "BULLISH BOS",
            "sweep_index": sweep_index,
            "sweep_extreme": sweep_extreme,
            "fvg_low": bull_fvg_low,
            "fvg_high": bull_fvg_high,
            "emas": emas,
            "confirmed_at": current.get("timestamp"),
        }

    if short_valid:
        sweep_index, sweep_extreme = recent_bear_sweeps[-1]
        return {
            "direction": "SHORT",
            "trend": trend,
            "atr": atr_value,
            "sweep_type": "BEARISH LIQUIDITY SWEEP",
            "bos_type": "BEARISH BOS",
            "sweep_index": sweep_index,
            "sweep_extreme": sweep_extreme,
            "fvg_low": bear_fvg_low,
            "fvg_high": bear_fvg_high,
            "emas": emas,
            "confirmed_at": current.get("timestamp"),
        }

    return None


# ============================================================
# POSITION SIZING
# ============================================================

def calculate_position(
    capital: float,
    entry: float,
    sl: float,
) -> dict[str, float]:
    capital = float(capital)
    entry = float(entry)
    sl = float(sl)

    if capital <= 0:
        raise ValueError("Капитал должен быть больше нуля.")
    if entry <= 0:
        raise ValueError("Entry должен быть больше нуля.")

    risk_distance = abs(entry - sl)
    if risk_distance <= 0:
        raise ValueError("Расстояние до SL должно быть больше нуля.")

    risk_usdt = capital * RISK_PERCENT / 100.0
    quantity_by_risk = risk_usdt / risk_distance

    max_position_value = capital * MAX_LEVERAGE
    quantity_by_leverage = max_position_value / entry
    quantity = min(quantity_by_risk, quantity_by_leverage)
    position_value = quantity * entry

    return {
        "capital": capital,
        "risk_usdt": risk_usdt,
        "quantity": quantity,
        "position_value": position_value,
        "max_position_value": max_position_value,
        "estimated_entry_fee": position_value * COMMISSION_PER_SIDE,
        "effective_stop_risk": quantity * risk_distance,
    }


# ============================================================
# ORDER CREATION
# ============================================================

def _make_order(
    direction: str,
    entry: float,
    sweep_extreme: float,
    atr_value: float,
    capital: float,
) -> dict[str, Any]:
    if direction == "LONG":
        sl = sweep_extreme - SL_ATR_BUFFER * atr_value
        if sl >= entry:
            sl = entry - atr_value
        risk_distance = entry - sl
        tp = entry + RISK_REWARD * risk_distance
    else:
        sl = sweep_extreme + SL_ATR_BUFFER * atr_value
        if sl <= entry:
            sl = entry + atr_value
        risk_distance = sl - entry
        tp = entry - RISK_REWARD * risk_distance

    if sl <= 0 or tp <= 0 or risk_distance <= 0:
        raise ValueError("Получены некорректные SL/TP.")

    position = calculate_position(capital, entry, sl)

    order = StrategyOrder(
        entry_number=1,
        direction=direction,
        order_type="MARKET_NEXT_CANDLE_OPEN",
        entry=entry,
        tp=tp,
        sl=sl,
        quantity=position["quantity"],
        position_value=position["position_value"],
        risk_usdt=position["effective_stop_risk"],
        rr=RISK_REWARD,
    )

    result = asdict(order)
    result.update({
        "estimated_entry_fee": position["estimated_entry_fee"],
        "estimated_exit_fee_at_tp": tp * position["quantity"] * COMMISSION_PER_SIDE,
        "max_position_value": position["max_position_value"],
        "target_risk_usdt": position["risk_usdt"],
        "leverage_cap_applied": (
            position["quantity"] * entry >= position["max_position_value"] * 0.999999
        ),
    })
    return result


# ============================================================
# STRATEGY ANALYSIS
# ============================================================

def analyze(
    candles: Sequence[dict[str, Any]],
    current_price: float | None = None,
    capital: float = INITIAL_CAPITAL,
    open_positions: int = 0,
    pending_orders: int = 0,
) -> dict[str, Any] | None:
    """
    MASB-style strategy interface.

    candles must be chronological and should contain only completed candles.
    Returns a signal proposal; this function does not place exchange orders.
    """
    normalized = _normalize_candles(candles)

    if len(normalized) < EMA_SLOW + 5:
        return None

    if current_price is None:
        current_price = normalized[-1]["close"]

    current_price = float(current_price)
    if current_price <= 0:
        raise ValueError("Текущая цена должна быть больше нуля.")
    if capital <= 0:
        raise ValueError("Капитал должен быть больше нуля.")
    if open_positions < 0 or pending_orders < 0:
        raise ValueError("Количество позиций и ордеров не может быть отрицательным.")

    # Keep one position/order per symbol for this strategy.
    if open_positions + pending_orders >= MAX_ENTRIES:
        return None

    setup = _analyze_structure(normalized)
    if setup is None:
        return None

    # The current price is used for sizing/display only. To avoid lookahead in
    # historical tests, a backtester should execute at the next candle's open.
    try:
        order = _make_order(
            direction=setup["direction"],
            entry=current_price,
            sweep_extreme=setup["sweep_extreme"],
            atr_value=setup["atr"],
            capital=capital,
        )
    except ValueError:
        return None

    signal = StrategySignal(
        strategy=NAME,
        direction=setup["direction"],
        timeframe=TIMEFRAME,
        current_price=current_price,
        trend=setup["trend"],
        atr=setup["atr"],
        sweep_type=setup["sweep_type"],
        bos_type=setup["bos_type"],
        fvg_low=setup["fvg_low"],
        fvg_high=setup["fvg_high"],
        orders=[order],
        confirmed_at=setup["confirmed_at"],
    )

    result = asdict(signal)
    result.update({
        "capital": float(capital),
        "risk_percent": RISK_PERCENT,
        "commission_per_side": COMMISSION_PER_SIDE,
        "initial_capital": INITIAL_CAPITAL,
        "risk_reward": RISK_REWARD,
        "max_leverage": MAX_LEVERAGE,
        "max_entries": MAX_ENTRIES,
        "ema_fast": setup["emas"]["ema_fast"],
        "ema_mid": setup["emas"]["ema_mid"],
        "ema_slow": setup["emas"]["ema_slow"],
        "lookahead_protection": "closed candles only; historical execution next candle open",
    })
    return result


# ============================================================
# TELEGRAM-FRIENDLY FORMAT
# ============================================================

def format_signal(signal: dict[str, Any] | None) -> str:
    if signal is None:
        return (
            f"🧠 MASB — {NAME}\n\n"
            "Нет нового подтверждённого сетапа.\n"
            "Ожидается совпадение Sweep + BOS + FVG + тренд."
        )

    direction = signal["direction"]
    emoji = "🟢" if direction == "LONG" else "🔴"
    lines = [
        f"🧠 MASB — {signal['strategy']}",
        "",
        f"Направление: {emoji} {direction}",
        f"Таймфрейм: {signal['timeframe']}",
        f"Тренд: {signal['trend']}",
        f"Сигнал: {signal['sweep_type']} + {signal['bos_type']} + FVG",
        f"FVG: {signal['fvg_low']:.8f} — {signal['fvg_high']:.8f}",
        f"ATR: {signal['atr']:.8f}",
        f"Текущая цена: {signal['current_price']:.8f}",
        f"Риск: {signal['risk_percent']:.2f}%",
        f"RR: 1:{signal['risk_reward']:.1f}",
        f"Максимальное плечо: {signal['max_leverage']:.1f}x",
        "",
    ]

    for order in signal["orders"]:
        lines.extend([
            f"Сделка №{order['entry_number']}",
            f"  Тип: {order['order_type']} {order['direction']}",
            f"  Entry: {order['entry']:.8f}",
            f"  TP: {order['tp']:.8f}",
            f"  SL: {order['sl']:.8f}",
            f"  RR: 1:{order['rr']:.1f}",
            f"  Номинал: {order['position_value']:.2f} USDT",
            f"  Количество: {order['quantity']:.8f}",
            f"  Риск до SL: {order['risk_usdt']:.4f} USDT",
            f"  Комиссия входа (оценка): {order['estimated_entry_fee']:.4f} USDT",
            f"  Лимит плеча применён: {'да' if order['leverage_cap_applied'] else 'нет'}",
            "",
        ])

    lines.extend([
        f"Подтверждение: {signal['confirmed_at']}",
        "Примечание: сигнал не размещает реальные ордера.",
    ])
    return "\n".join(lines)


if __name__ == "__main__":
    print(f"Стратегия {NAME}")
    print(f"Таймфрейм: {TIMEFRAME}")
    print(f"Начальный капитал: {INITIAL_CAPITAL:.2f} USDT")
    print(f"Риск на сделку: {RISK_PERCENT:.2f}%")
    print(f"RR: 1:{RISK_REWARD:.1f}")
    print(f"Комиссия за сторону: {COMMISSION_PER_SIDE * 100:.3f}%")
    print(f"Максимальное плечо: {MAX_LEVERAGE:.1f}x")
    print("Стратегия загружена. Для анализа передай закрытые свечи в analyze().")
