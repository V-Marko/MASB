from __future__ import annotations

from math import isfinite
from typing import Any, Sequence

NAME = "obHunter"
DESCRIPTION = "OB Hunter: Order Block + Liquidity Sweep + BOS + EMA trend + ATR (1H, virtual trades)"
TIMEFRAME = "1h"

INITIAL_CAPITAL = 100.0
RISK_PERCENT = 3.0
RISK_REWARD = 3.0
MAX_LEVERAGE = 5.0
COMMISSION_PER_SIDE = 0.00055
EMA_FAST = 50
EMA_SLOW = 200
ATR_PERIOD = 14
SWING_LOOKBACK = 12
OB_SEARCH_BARS = 8
DISPLACEMENT_ATR = 0.8
SL_ATR_BUFFER = 0.15
MAX_ENTRIES = 1


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out: list[float] = []
    value = values[0]
    for item in values:
        value = alpha * item + (1.0 - alpha) * value
        out.append(value)
    return out


def _atr(candles: list[dict[str, float]], period: int) -> list[float]:
    trs: list[float] = []
    for i, candle in enumerate(candles):
        if i == 0:
            tr = candle["high"] - candle["low"]
        else:
            prev_close = candles[i - 1]["close"]
            tr = max(
                candle["high"] - candle["low"],
                abs(candle["high"] - prev_close),
                abs(candle["low"] - prev_close),
            )
        trs.append(tr)
    return _ema(trs, period)


def analyze(
    candles: Sequence[dict[str, Any]],
    current_price: float | None = None,
    capital: float = INITIAL_CAPITAL,
    open_positions: int = 0,
    pending_orders: int = 0,
) -> dict[str, Any] | None:
    """Return a MASB-compatible virtual limit order using closed candles only."""
    if open_positions + pending_orders >= MAX_ENTRIES or capital <= 0:
        return None
    if len(candles) < max(EMA_SLOW + 5, SWING_LOOKBACK + OB_SEARCH_BARS + 5):
        return None

    data: list[dict[str, float]] = []
    for item in candles:
        row = {key: float(item[key]) for key in ("open", "high", "low", "close")}
        if row["low"] <= 0 or row["high"] < row["low"]:
            return None
        data.append(row)

    # Caller supplies closed candles; no future candle is referenced.
    close_values = [c["close"] for c in data]
    ema_fast = _ema(close_values, EMA_FAST)[-1]
    ema_slow = _ema(close_values, EMA_SLOW)[-1]
    current = data[-1]
    price = float(current_price if current_price is not None else current["close"])
    if price <= 0:
        return None

    trend = "BULLISH" if ema_fast > ema_slow and current["close"] > ema_slow else (
        "BEARISH" if ema_fast < ema_slow and current["close"] < ema_slow else "NEUTRAL"
    )
    if trend == "NEUTRAL":
        return None

    atr_values = _atr(data, ATR_PERIOD)
    atr = atr_values[-1]
    if not isfinite(atr) or atr <= 0:
        return None

    # Recent structure is built only from candles preceding the signal candle.
    prior = data[-SWING_LOOKBACK-1:-1]
    if len(prior) < SWING_LOOKBACK:
        return None
    prior_high = max(c["high"] for c in prior)
    prior_low = min(c["low"] for c in prior)

    direction: str
    ob_index: int
    ob_low: float
    ob_high: float
    sweep_extreme: float

    if trend == "BULLISH":
        # Liquidity sweep below a prior low, followed by a bullish BOS close.
        sweep_idx = None
        for i in range(len(data) - 2, max(EMA_SLOW, len(data) - 7), -1):
            window = data[max(0, i - SWING_LOOKBACK):i]
            if len(window) < SWING_LOOKBACK:
                continue
            level = min(c["low"] for c in window)
            if data[i]["low"] < level and data[i]["close"] > level:
                sweep_idx = i
                break
        if sweep_idx is None or current["close"] <= prior_high:
            return None
        # Last bearish candle before the BOS impulse is the bullish order block.
        candidates = range(max(sweep_idx + 1, len(data) - OB_SEARCH_BARS - 1), len(data) - 1)
        ob_index = -1
        for i in reversed(list(candidates)):
            candle = data[i]
            if candle["close"] < candle["open"]:
                ob_index = i
                break
        if ob_index < 0 or current["close"] - data[ob_index]["high"] < atr * DISPLACEMENT_ATR:
            return None
        direction = "LONG"
        ob_low, ob_high = data[ob_index]["low"], data[ob_index]["high"]
        sweep_extreme = min(c["low"] for c in data[sweep_idx:ob_index + 1])
        # Limit entry at OB midpoint; require price still above the zone.
        entry = (ob_low + ob_high) / 2.0
        if price <= entry:
            return None
        sl = min(ob_low, sweep_extreme) - atr * SL_ATR_BUFFER
        risk_distance = entry - sl
        tp = entry + risk_distance * RISK_REWARD
    else:
        # Liquidity sweep above a prior high, followed by a bearish BOS close.
        sweep_idx = None
        for i in range(len(data) - 2, max(EMA_SLOW, len(data) - 7), -1):
            window = data[max(0, i - SWING_LOOKBACK):i]
            if len(window) < SWING_LOOKBACK:
                continue
            level = max(c["high"] for c in window)
            if data[i]["high"] > level and data[i]["close"] < level:
                sweep_idx = i
                break
        if sweep_idx is None or current["close"] >= prior_low:
            return None
        candidates = range(max(sweep_idx + 1, len(data) - OB_SEARCH_BARS - 1), len(data) - 1)
        ob_index = -1
        for i in reversed(list(candidates)):
            candle = data[i]
            if candle["close"] > candle["open"]:
                ob_index = i
                break
        if ob_index < 0 or data[ob_index]["low"] - current["close"] < atr * DISPLACEMENT_ATR:
            return None
        direction = "SHORT"
        ob_low, ob_high = data[ob_index]["low"], data[ob_index]["high"]
        sweep_extreme = max(c["high"] for c in data[sweep_idx:ob_index + 1])
        entry = (ob_low + ob_high) / 2.0
        if price >= entry:
            return None
        sl = max(ob_high, sweep_extreme) + atr * SL_ATR_BUFFER
        risk_distance = sl - entry
        tp = entry - risk_distance * RISK_REWARD

    if risk_distance <= 0 or tp <= 0:
        return None

    risk_usdt = capital * RISK_PERCENT / 100.0
    quantity = risk_usdt / risk_distance
    max_notional = capital * MAX_LEVERAGE
    quantity = min(quantity, max_notional / entry)
    if quantity <= 0 or not isfinite(quantity):
        return None

    order = {
        "entry_number": 1,
        "entry_fib": 0.0,
        "tp_fib": 0.0,
        "is_green_tp": False,
        "order_type": "LIMIT",
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "quantity": quantity,
        "position_value": quantity * entry,
        "risk_usdt": risk_usdt,
        "rr": RISK_REWARD,
        "ob_low": ob_low,
        "ob_high": ob_high,
    }

    # trade_monitor.add_signal_orders expects swing_start/swing_end metadata.
    return {
        "strategy": NAME,
        "direction": direction,
        "timeframe": TIMEFRAME,
        "current_price": price,
        "trend": trend,
        "atr": atr,
        "order_block_low": ob_low,
        "order_block_high": ob_high,
        "sweep_index": sweep_idx,
        "bos_index": len(data) - 1,
        "swing_start": ob_low,
        "swing_end": ob_high,
        "swing_start_index": ob_index,
        "swing_end_index": len(data) - 1,
        "swing_start_timestamp": candles[ob_index].get("timestamp", candles[ob_index].get("time")),
        "swing_end_timestamp": candles[-1].get("timestamp", candles[-1].get("time")),
        "orders": [order],
        "rr": RISK_REWARD,
        "risk_percent": RISK_PERCENT,
        "max_leverage": MAX_LEVERAGE,
        "commission_per_side": COMMISSION_PER_SIDE,
        "note": "Paper trade only; OB + liquidity sweep + BOS + EMA trend + ATR.",
    }


def format_signal(signal: dict[str, Any] | None) -> str:
    if signal is None:
        return f"{NAME}: подходящего подтверждённого сетапа нет."
    order = signal["orders"][0]
    return (
        f"🎯 {NAME} | {signal['direction']} | {TIMEFRAME}\n"
        f"Тренд: {signal['trend']}\n"
        f"Order Block: {signal['order_block_low']:.8g}–{signal['order_block_high']:.8g}\n"
        f"Entry: {order['entry']:.8g}\nTP: {order['tp']:.8g}\nSL: {order['sl']:.8g}\n"
        f"RR: 1:{RISK_REWARD:g} | Риск: {RISK_PERCENT:g}% | Плечо максимум: {MAX_LEVERAGE:g}x\n"
        "Виртуальная сделка, реальные ордера не отправляются."
    )
