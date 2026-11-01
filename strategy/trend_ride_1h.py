from __future__ import annotations

from typing import Any, Sequence

NAME = "TrendRide_1H"
DESCRIPTION = (
    "Trend following 1H: EMA 50/200 + ADX 14, ATR stop, "
    "ATR trailing stop, trend-reversal exit, max hold 72h"
)

TIMEFRAME = "1h"
RISK_FRACTION = 0.01
MAX_LEVERAGE = 5.0
COMMISSION_PER_SIDE = 0.00055
ADX_MIN = 20.0
ATR_PERIOD = 14
INITIAL_SL_ATR = 2.5
TRAILING_ATR = 2.8
MAX_HOLD_HOURS = 72
TP_ATR_CAP = 12.0


def _ema(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1.0 - alpha) * result[-1])
    return result


def _indicators(candles: Sequence[dict[str, Any]]) -> tuple[list[float], list[float], list[float], list[float]]:
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200)

    tr: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(len(candles)):
        if i == 0:
            tr.append(highs[i] - lows[i])
            plus_dm.append(0.0)
            minus_dm.append(0.0)
            continue
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    def wilder(values: list[float], period: int) -> list[float]:
        out = [0.0] * len(values)
        if len(values) <= period:
            return out
        out[period] = sum(values[1:period + 1]) / period
        for j in range(period + 1, len(values)):
            out[j] = (out[j - 1] * (period - 1) + values[j]) / period
        return out

    atr = wilder(tr, ATR_PERIOD)
    smooth_tr = wilder(tr, 14)
    smooth_plus = wilder(plus_dm, 14)
    smooth_minus = wilder(minus_dm, 14)
    dx = [0.0] * len(candles)
    for i in range(len(candles)):
        if smooth_tr[i] <= 0:
            continue
        pdi = 100.0 * smooth_plus[i] / smooth_tr[i]
        mdi = 100.0 * smooth_minus[i] / smooth_tr[i]
        denom = pdi + mdi
        dx[i] = 100.0 * abs(pdi - mdi) / denom if denom else 0.0
    adx = wilder(dx, 14)
    return ema50, ema200, atr, adx


def analyze(
    candles: Sequence[dict[str, Any]],
    current_price: float | None = None,
    capital: float = 100.0,
    open_positions: int = 0,
    pending_orders: int = 0,
) -> dict[str, Any] | None:
    if len(candles) < 230 or open_positions + pending_orders > 0 or capital <= 0:
        return None

    try:
        rows = [
            {
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "timestamp": c.get("timestamp", c.get("time")),
            }
            for c in candles
        ]
    except (KeyError, TypeError, ValueError):
        return None

    if any(r["low"] <= 0 or r["high"] < r["low"] for r in rows):
        return None

    ema50, ema200, atr, adx = _indicators(rows)
    i = len(rows) - 1
    close = float(current_price if current_price is not None else rows[i]["close"])
    volatility = atr[i]
    if volatility <= 0 or adx[i] < ADX_MIN:
        return None

    rising = ema50[i] > ema50[i - 3]
    falling = ema50[i] < ema50[i - 3]

    if close > ema50[i] > ema200[i] and rising:
        direction = "LONG"
        sl = close - INITIAL_SL_ATR * volatility
        tp = close + TP_ATR_CAP * volatility
    elif close < ema50[i] < ema200[i] and falling:
        direction = "SHORT"
        sl = close + INITIAL_SL_ATR * volatility
        tp = close - TP_ATR_CAP * volatility
    else:
        return None

    risk_distance = abs(close - sl)
    if risk_distance <= 0 or tp <= 0:
        return None

    risk_cash = float(capital) * RISK_FRACTION
    quantity_risk = risk_cash / risk_distance
    quantity_leverage = (float(capital) * MAX_LEVERAGE) / close
    quantity = min(quantity_risk, quantity_leverage)
    if quantity <= 0:
        return None

    timestamp = rows[i]["timestamp"]
    start_index = max(0, i - 199)
    start_timestamp = rows[start_index]["timestamp"]
    order = {
        "entry_number": 1,
        "direction": direction,
        "entry": close,
        "tp": tp,
        "sl": sl,
        "quantity": quantity,
        "position_value": quantity * close,
        "entry_fib": 0.0,
        "tp_fib": 0.0,
        "is_green_tp": False,
        "rr": TP_ATR_CAP / INITIAL_SL_ATR,
        "management": {
            "mode": "trend_following",
            "max_hold_hours": MAX_HOLD_HOURS,
            "trailing_atr": TRAILING_ATR,
            "ema_exit_period": 50,
            "ema_exit_confirmations": 2,
            "adx_min": ADX_MIN,
        },
    }
    return {
        "strategy": NAME,
        "direction": direction,
        "timeframe": TIMEFRAME,
        "entry": close,
        "tp": tp,
        "sl": sl,
        "rr": TP_ATR_CAP / INITIAL_SL_ATR,
        "swing_start": float(ema200[i]),
        "swing_end": close,
        "swing_start_index": start_index,
        "swing_end_index": i,
        "swing_start_timestamp": start_timestamp,
        "swing_end_timestamp": timestamp,
        "current_price": close,
        "ema50": ema50[i],
        "ema200": ema200[i],
        "adx": adx[i],
        "atr": volatility,
        "risk_fraction": RISK_FRACTION,
        "max_leverage": MAX_LEVERAGE,
        "commission_per_side": COMMISSION_PER_SIDE,
        "orders": [order],
        "signal_only": True,
    }


def format_signal(signal: dict[str, Any] | None) -> str:
    if signal is None:
        return "TrendRide 1H: нет подтверждённого тренда."
    return (
        f"📈 {NAME} | {signal['direction']}\n"
        f"Entry: {signal['entry']:.8g}\n"
        f"SL: {signal['sl']:.8g} (2.5 ATR)\n"
        f"EMA 50/200: {signal['ema50']:.8g} / {signal['ema200']:.8g}\n"
        f"ADX: {signal['adx']:.1f}\n"
        "Управление: ATR trailing stop, разворот тренда, максимум 72 часа. "
        "Виртуальная сделка."
    )
