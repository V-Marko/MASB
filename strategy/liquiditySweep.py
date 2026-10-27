
from __future__ import annotations
from typing import Any, Sequence

NAME = "liquiditySweep"
DESCRIPTION = "Liquidity Sweep + CHoCH (1H, paper signals, RR 1:3)"
TIMEFRAME = "1h"
RR = 3.0
PIVOT_BARS = 2
LOOKBACK = 80
MAX_CONFIRM_BARS = 12
RISK_FRACTION = 0.005


def analyze(candles: Sequence[dict[str, Any]], current_price: float | None = None,
            capital: float = 100.0, open_positions: int = 0,
            pending_orders: int = 0) -> dict[str, Any] | None:
    if open_positions or pending_orders or len(candles) < 25:
        return None
    c = [{k: float(x[k]) for k in ("open", "high", "low", "close")} for x in candles]
    if any(x["low"] <= 0 or x["high"] < x["low"] for x in c):
        return None
    n = len(c)
    highs, lows = [], []
    for i in range(max(PIVOT_BARS, n - LOOKBACK), n - PIVOT_BARS):
        segment = c[i-PIVOT_BARS:i+PIVOT_BARS+1]
        if c[i]["high"] == max(x["high"] for x in segment):
            highs.append(i)
        if c[i]["low"] == min(x["low"] for x in segment):
            lows.append(i)
    for s in range(n-2, max(PIVOT_BARS+1, n-MAX_CONFIRM_BARS-2), -1):
        prior_highs = [i for i in highs if i+PIVOT_BARS < s]
        prior_lows = [i for i in lows if i+PIVOT_BARS < s]
        if not prior_highs or not prior_lows:
            continue
        hi, lo = c[prior_highs[-1]]["high"], c[prior_lows[-1]]["low"]
        sweep_long = c[s]["low"] < lo and c[s]["close"] > lo
        sweep_short = c[s]["high"] > hi and c[s]["close"] < hi
        if not (sweep_long or sweep_short):
            continue
        direction = "LONG" if sweep_long else "SHORT"
        structure = hi if sweep_long else lo
        if direction == "LONG":
            confirmed = c[-1]["close"] > structure and all(c[j]["close"] <= structure for j in range(s+1, n-1))
            stop = min(x["low"] for x in c[s:])
        else:
            confirmed = c[-1]["close"] < structure and all(c[j]["close"] >= structure for j in range(s+1, n-1))
            stop = max(x["high"] for x in c[s:])
        if not confirmed:
            continue
        entry = c[-1]["close"]
        risk = (entry-stop) if direction == "LONG" else (stop-entry)
        if risk <= 0 or capital <= 0:
            return None
        tp = entry + RR*risk if direction == "LONG" else entry - RR*risk
        if tp <= 0:
            return None
        qty = capital * RISK_FRACTION / risk
        return {"strategy": NAME, "direction": direction, "timeframe": TIMEFRAME,
                "entry": entry, "sl": stop, "tp": tp, "rr": RR,
                "sweep_index": s, "confirmation_index": n-1,
                "structure_level": structure, "risk_per_unit": risk,
                "quantity": qty, "position_value": qty*entry,
                "orders": [], "signal_only": True,
                "note": "Paper signal only. Assumes next execution at confirmation close; backtest must account for slippage and fees."}
    return None


def format_signal(signal: dict[str, Any] | None) -> str:
    if signal is None:
        return "Liquidity Sweep + CHoCH (1H): подтверждённого сигнала нет."
    return (f"Liquidity Sweep + CHoCH | {signal['direction']} | 1H\n"
            f"Entry: {signal['entry']:.8f}\nSL: {signal['sl']:.8f}\n"
            f"TP: {signal['tp']:.8f}\nRR: 1:{signal['rr']:g}\n"
            "Только тестовый сигнал — ордер не отправляется.")
