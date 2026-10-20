"""Shared ATR spike + 3-candle FVG signal logic. Signal generation only."""
from __future__ import annotations

from typing import Any, Sequence

TIMEFRAME = "1h"  # Matches MASB's existing Binance data feed.
ATR_PERIOD = 14
SPIKE_MULTIPLIER = 3.0
RR = 3.0
RISK_PERCENT = 1.0
MAX_LEVERAGE = 3.0
STOP_BUFFER_ATR = 0.10
MAX_SETUP_AGE = 12
STRUCTURE_LOOKBACK = 5


def _atr(candles: list[dict], end: int) -> float | None:
    """Simple mean of prior true ranges, excludes impulse candle."""
    if end < ATR_PERIOD:
        return None
    ranges = []
    for i in range(end - ATR_PERIOD, end):
        c = candles[i]
        prev = candles[i - 1]["close"] if i else c["close"]
        ranges.append(max(c["high"] - c["low"], abs(c["high"] - prev), abs(c["low"] - prev)))
    return sum(ranges) / ATR_PERIOD


def _find_setup(candles: list[dict], last: int):
    # i = middle candle of FVG; i+1 is its confirmation candle.
    for i in range(last - 2, max(ATR_PERIOD, last - MAX_SETUP_AGE - 1), -1):
        if i + 1 >= last:
            continue
        first, impulse, third = candles[i - 1], candles[i], candles[i + 1]
        atr = _atr(candles, i)
        if not atr or impulse["high"] - impulse["low"] < SPIKE_MULTIPLIER * atr:
            continue
        if impulse["close"] > impulse["open"] and third["low"] > first["high"]:
            direction, gap_low, gap_high = "SHORT", first["high"], third["low"]
            stop = max(impulse["high"], third["high"]) + STOP_BUFFER_ATR * atr
        elif impulse["close"] < impulse["open"] and third["high"] < first["low"]:
            direction, gap_low, gap_high = "LONG", third["high"], first["low"]
            stop = min(impulse["low"], third["low"]) - STOP_BUFFER_ATR * atr
        else:
            continue
        # Do not reuse a gap that was fully filled before the current candle.
        between = candles[i + 2:last]
        if direction == "SHORT" and any(c["low"] <= gap_low for c in between):
            continue
        if direction == "LONG" and any(c["high"] >= gap_high for c in between):
            continue
        return {"direction": direction, "gap_low": gap_low, "gap_high": gap_high,
                "stop": stop, "atr": atr, "impulse_index": i}
    return None


def analyze_variant(
    variant: str,
    name: str,
    candles: Sequence[dict[str, Any]],
    current_price: float | None = None,
    capital: float = 100.0,
    open_positions: int = 0,
    pending_orders: int = 0,
) -> dict[str, Any] | None:
    if open_positions or pending_orders or capital <= 0 or len(candles) < ATR_PERIOD + 5:
        return None
    data = []
    for c in candles:
        x = {k: float(c[k]) for k in ("open", "high", "low", "close")}
        if x["low"] <= 0 or x["high"] < max(x["open"], x["close"]) or x["low"] > min(x["open"], x["close"]):
            return None
        x["timestamp"] = c.get("timestamp", c.get("time"))
        data.append(x)
    last = len(data) - 1
    current = data[last]
    # Only signal at last closed candle; never rely on an unfinished live candle.
    if current_price is not None and abs(float(current_price) - current["close"]) > 1e-8 * current["close"]:
        return None
    setup = _find_setup(data, last)
    if setup is None:
        return None
    direction = setup["direction"]
    gap_low, gap_high = setup["gap_low"], setup["gap_high"]
    # Current candle must overlap the imbalance zone.
    touch = current["low"] <= gap_high and current["high"] >= gap_low
    if not touch:
        return None
    if variant == "B":
        # Rejection confirmation: candle closes in reversal direction
        # and beyond the middle of the FVG.
        midpoint = (gap_low + gap_high) / 2
        if direction == "SHORT" and not (current["close"] < current["open"] and current["close"] < midpoint):
            return None
        if direction == "LONG" and not (current["close"] > current["open"] and current["close"] > midpoint):
            return None
    if variant == "C":
        # Break last N completed candle extremes (excluding signal candle).
        previous = data[max(0, last - STRUCTURE_LOOKBACK):last]
        if len(previous) < STRUCTURE_LOOKBACK:
            return None
        if direction == "SHORT" and not current["close"] < min(x["low"] for x in previous):
            return None
        if direction == "LONG" and not current["close"] > max(x["high"] for x in previous):
            return None

    entry, stop = current["close"], setup["stop"]
    risk = (entry - stop) if direction == "LONG" else (stop - entry)
    if risk <= 0:
        return None
    tp = entry + RR * risk if direction == "LONG" else entry - RR * risk
    if tp <= 0:
        return None
    risk_amount = float(capital) * RISK_PERCENT / 100.0
    qty = min(risk_amount / risk, float(capital) * MAX_LEVERAGE / entry)
    if qty <= 0:
        return None
    order = {
        "direction": direction, "entry": entry, "sl": stop, "tp": tp,
        "rr": RR, "quantity": qty, "position_value": qty * entry,
        "risk_amount": qty * risk, "status": "SIGNAL_ONLY",
        "gap_low": gap_low, "gap_high": gap_high,
    }
    return {
        "strategy": name, "direction": direction, "timeframe": TIMEFRAME,
        "entry": entry, "sl": stop, "tp": tp, "rr": RR,
        "risk_percent": RISK_PERCENT, "quantity": qty,
        "position_value": qty * entry, "atr": setup["atr"],
        "fvg_low": gap_low, "fvg_high": gap_high,
        "impulse_index": setup["impulse_index"],
        "confirmed_at": last, "timestamp": current["timestamp"],
        "current_price": entry, "orders": [order],
        "mode": "SIGNAL_ONLY",
    }


def format_variant(name: str, signal: dict | None) -> str:
    if not signal:
        return f"{name}: нет нового подтверждённого сигнала."
    return (
        f"{name} | {signal['timeframe']} | {signal['direction']}\n"
        f"Entry: {signal['entry']:.8f}\nSL: {signal['sl']:.8f}\n"
        f"TP: {signal['tp']:.8f} | RR 1:3\n"
        f"FVG: {signal['fvg_low']:.8f} — {signal['fvg_high']:.8f}\n"
        "Исследовательский сигнал, не реальный ордер."
    )


NAME = "atrFvgA"
DESCRIPTION = "ATR Spike + FVG, version A (RR 1:3)"

def analyze(candles, current_price=None, capital=100.0, open_positions=0, pending_orders=0):
    return analyze_variant("A", NAME, candles, current_price, capital, open_positions, pending_orders)

def format_signal(signal):
    return format_variant(NAME, signal)
