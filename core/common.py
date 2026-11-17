from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

INITIAL_CAPITAL = 100.0
POSITION_CAPITAL_PERCENT = 50.0
COMMISSION_PER_SIDE = 0.00055
RISK_REWARD = 3.0


@dataclass
class StrategySignal:
    strategy: str
    direction: str
    entry: float
    tp: float
    sl: float
    quantity: float
    position_value: float
    estimated_entry_fee: float
    rr: float
    reason: str
    status: str = "SIGNAL"


def normalize_candles(candles: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in candles:
        row = {
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c.get("volume", 0.0)),
            "timestamp": c.get("timestamp", c.get("time")),
        }
        if row["close"] <= 0 or row["high"] <= 0 or row["low"] <= 0:
            raise ValueError("Цены свечи должны быть положительными.")
        if row["high"] < row["low"]:
            raise ValueError("High не может быть ниже Low.")
        out.append(row)
    return out


def sma(values: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema_series(values: Sequence[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("Период EMA должен быть положительным.")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for i in range(period, len(values)):
        current = values[i] * alpha + current * (1.0 - alpha)
        result[i] = current
    return result


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    gains, losses = [], []
    for i in range(len(values) - period, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(candles: Sequence[dict[str, Any]], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(len(candles) - period, len(candles)):
        c, prev = candles[i], candles[i - 1]
        trs.append(max(
            c["high"] - c["low"],
            abs(c["high"] - prev["close"]),
            abs(c["low"] - prev["close"]),
        ))
    return sum(trs) / period


def make_signal(
    strategy: str,
    direction: str,
    entry: float,
    sl: float,
    capital: float,
    reason: str,
    rr: float = RISK_REWARD,
) -> dict[str, Any]:
    if capital <= 0 or entry <= 0:
        raise ValueError("Капитал и цена входа должны быть больше нуля.")
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("Направление должно быть LONG или SHORT.")
    risk = abs(entry - sl)
    if risk <= 0:
        raise ValueError("Stop Loss должен отличаться от Entry.")
    tp = entry + rr * risk if direction == "LONG" else entry - rr * risk
    if tp <= 0:
        raise ValueError("Расчётный TP получился неположительным.")
    value = capital * POSITION_CAPITAL_PERCENT / 100.0
    qty = value / entry
    signal = StrategySignal(
        strategy=strategy,
        direction=direction,
        entry=entry,
        tp=tp,
        sl=sl,
        quantity=qty,
        position_value=value,
        estimated_entry_fee=value * COMMISSION_PER_SIDE,
        rr=rr,
        reason=reason,
    )
    result = asdict(signal)
    result.update({
        "capital": float(capital),
        "initial_capital": INITIAL_CAPITAL,
        "position_capital_percent": POSITION_CAPITAL_PERCENT,
        "commission_per_side": COMMISSION_PER_SIDE,
    })
    return result


def format_signal(signal: dict[str, Any] | None, name: str) -> str:
    if signal is None:
        return f"📊 {name}\n\nНет подходящего сетапа."
    return "\n".join([
        f"📊 MASB — {signal['strategy']}",
        f"Направление: {signal['direction']}",
        f"Причина: {signal['reason']}",
        f"Entry: {signal['entry']:.8f}",
        f"TP: {signal['tp']:.8f}",
        f"SL: {signal['sl']:.8f}",
        f"RR: 1:{signal['rr']:.2f}",
        f"Номинал: {signal['position_value']:.2f} USDT",
        f"Количество: {signal['quantity']:.8f}",
        f"Комиссия входа (оценка): {signal['estimated_entry_fee']:.4f} USDT",
        "Режим: только сигнал, ордера не отправляются.",
    ])
