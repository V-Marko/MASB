from __future__ import annotations

from typing import Any, Callable, Sequence

from core.zigzag import find_confirmed_swings

RR = 3.0
RISK_FRACTION = 0.01
MAX_LEVERAGE = 5.0
COMMISSION_PER_SIDE = 0.00055
SWEEP_LOOKBACK = 24
SL_BUFFER_FRACTION = 0.001


def build_analyzer(name: str, deviation: float) -> Callable[..., dict[str, Any] | None]:
    """Build a confirmed ZigZag + liquidity sweep + BOS analyzer.

    Only confirmed ZigZag pivots and completed candle data are used.
    The resulting order is a virtual limit entry at the BOS close.
    """

    def analyze(
        candles: Sequence[dict[str, Any]],
        current_price: float | None = None,
        capital: float = 100.0,
        open_positions: int = 0,
        pending_orders: int = 0,
    ) -> dict[str, Any] | None:
        if len(candles) < 80 or open_positions + pending_orders > 0 or capital <= 0:
            return None

        data: list[dict[str, float]] = []
        timestamps: list[Any] = []
        for candle in candles:
            try:
                row = {key: float(candle[key]) for key in ("open", "high", "low", "close")}
                if row["low"] <= 0 or row["high"] < row["low"]:
                    return None
                data.append(row)
                timestamps.append(candle.get("timestamp", candle.get("time")))
            except (KeyError, TypeError, ValueError):
                return None

        price = float(current_price if current_price is not None else data[-1]["close"])
        if price <= 0:
            return None

        swings = find_confirmed_swings(data, deviation_percent=deviation)
        if len(swings) < 3:
            return None

        last_index = len(data) - 1
        first_search = max(1, last_index - SWEEP_LOOKBACK)
        # Evaluate the most recent sweep first. The break must happen after it.
        for sweep_index in range(last_index - 1, first_search - 1, -1):
            prior_swings = [p for p in swings if p.confirmed_at < sweep_index]
            if len(prior_swings) < 2:
                continue

            prior_highs = [p for p in prior_swings if p.kind == "HIGH"]
            prior_lows = [p for p in prior_swings if p.kind == "LOW"]
            if not prior_highs or not prior_lows:
                continue

            candle = data[sweep_index]
            low_pivot = prior_lows[-1]
            high_pivot = prior_highs[-1]

            long_sweep = candle["low"] < low_pivot.price and candle["close"] > low_pivot.price
            short_sweep = candle["high"] > high_pivot.price and candle["close"] < high_pivot.price
            if not (long_sweep or short_sweep):
                continue

            direction = "LONG" if long_sweep else "SHORT"
            structure_pivot = prior_highs[-1] if long_sweep else prior_lows[-1]
            # BOS must be a later candle close beyond the last opposite pivot.
            break_index = None
            for idx in range(sweep_index + 1, len(data)):
                close = data[idx]["close"]
                if (direction == "LONG" and close > structure_pivot.price) or (
                    direction == "SHORT" and close < structure_pivot.price
                ):
                    break_index = idx
                    break
            if break_index is None or break_index != last_index:
                continue

            entry = data[break_index]["close"]
            if direction == "LONG":
                sweep_extreme = min(row["low"] for row in data[sweep_index:break_index + 1])
                sl = sweep_extreme - entry * SL_BUFFER_FRACTION
                risk_distance = entry - sl
                tp = entry + RR * risk_distance
                swing_start, swing_end = low_pivot.price, structure_pivot.price
                swing_start_index, swing_end_index = low_pivot.index, structure_pivot.index
            else:
                sweep_extreme = max(row["high"] for row in data[sweep_index:break_index + 1])
                sl = sweep_extreme + entry * SL_BUFFER_FRACTION
                risk_distance = sl - entry
                tp = entry - RR * risk_distance
                swing_start, swing_end = high_pivot.price, structure_pivot.price
                swing_start_index, swing_end_index = high_pivot.index, structure_pivot.index

            if risk_distance <= 0 or tp <= 0:
                continue

            risk_cash = float(capital) * RISK_FRACTION
            quantity_by_risk = risk_cash / risk_distance
            max_notional = float(capital) * MAX_LEVERAGE
            quantity_by_leverage = max_notional / entry
            quantity = min(quantity_by_risk, quantity_by_leverage)
            if quantity <= 0:
                continue

            order = {
                "entry_number": 1,
                "direction": direction,
                "entry": entry,
                "tp": tp,
                "sl": sl,
                "quantity": quantity,
                "position_value": quantity * entry,
                "entry_fib": 0.0,
                "tp_fib": 0.0,
                "is_green_tp": False,
                "rr": RR,
                "risk_usdt": quantity * risk_distance,
                "estimated_entry_fee": quantity * entry * COMMISSION_PER_SIDE,
            }
            return {
                "strategy": name,
                "direction": direction,
                "zigzag_deviation": deviation,
                "timeframe": "1h",
                "entry": entry,
                "tp": tp,
                "sl": sl,
                "rr": RR,
                "swing_start": float(swing_start),
                "swing_end": float(swing_end),
                "swing_start_index": int(swing_start_index),
                "swing_end_index": int(swing_end_index),
                "swing_start_timestamp": timestamps[swing_start_index],
                "swing_end_timestamp": timestamps[swing_end_index],
                "confirmed_at": int(break_index),
                "current_price": price,
                "capital": float(capital),
                "risk_fraction": RISK_FRACTION,
                "max_leverage": MAX_LEVERAGE,
                "commission_per_side": COMMISSION_PER_SIDE,
                "sweep_index": int(sweep_index),
                "bos_index": int(break_index),
                "liquidity_level": float(low_pivot.price if direction == "LONG" else high_pivot.price),
                "structure_level": float(structure_pivot.price),
                "sweep_extreme": float(sweep_extreme),
                "orders": [order],
                "signal_only": True,
            }
        return None

    return analyze


def format_signal(signal: dict[str, Any] | None) -> str:
    if signal is None:
        return "Smart Money ZZ: подтверждённого Sweep + BOS сетапа нет."
    return (
        f"🧠 {signal['strategy']} | {signal['direction']} | 1H\n"
        f"ZZ: {signal['zigzag_deviation']:g}%\n"
        f"Entry: {signal['entry']:.8g}\n"
        f"TP: {signal['tp']:.8g}\n"
        f"SL: {signal['sl']:.8g}\n"
        f"RR: 1:{signal['rr']:g} | риск: {signal['risk_fraction'] * 100:.2g}%\n"
        f"Sweep → BOS подтверждён. Только виртуальная сделка."
    )
