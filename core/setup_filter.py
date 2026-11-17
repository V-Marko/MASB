from __future__ import annotations

from math import isfinite
from typing import Any

# Hard execution guard shared by every automatically discovered strategy.
MAX_ENTRY_DISTANCE_PCT = 5.0
MIN_REWARD_RISK = 1.5


def filter_signal_orders(
    signal: dict[str, Any] | None,
    current_price: float,
    *,
    max_entry_distance_pct: float = MAX_ENTRY_DISTANCE_PCT,
    min_reward_risk: float = MIN_REWARD_RISK,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Keep only executable, correctly-oriented orders close to market.

    This is a final safety/quality gate, not a signal generator. It does not
    change strategy indicators or fabricate entries, stops, or targets.
    """
    if not isinstance(signal, dict):
        return None, ["Стратегия не вернула корректный сигнал"]

    try:
        price = float(current_price)
    except (TypeError, ValueError):
        return None, ["Текущая цена некорректна"]
    if not isfinite(price) or price <= 0:
        return None, ["Текущая цена должна быть положительной"]

    direction = str(signal.get("direction", "")).upper()
    if direction not in {"LONG", "SHORT"}:
        return None, ["Неизвестное направление сделки"]

    raw_orders = signal.get("orders")
    if not isinstance(raw_orders, list) or not raw_orders:
        return None, ["Стратегия не сформировала ордера"]

    accepted: list[dict[str, Any]] = []
    rejected: list[str] = []
    for index, raw in enumerate(raw_orders, start=1):
        if not isinstance(raw, dict):
            rejected.append(f"Ордер #{index}: неверный формат")
            continue
        try:
            entry = float(raw["entry"])
            tp = float(raw["tp"])
            sl = float(raw["sl"])
            if not all(isfinite(v) and v > 0 for v in (entry, tp, sl)):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            rejected.append(f"Ордер #{index}: Entry/TP/SL некорректны")
            continue

        distance_pct = abs(entry - price) / price * 100.0
        if distance_pct > max_entry_distance_pct + 1e-9:
            rejected.append(
                f"Ордер #{index}: Entry дальше лимита {max_entry_distance_pct:g}% "
                f"(расстояние {distance_pct:.2f}%)"
            )
            continue

        # A resting LIMIT must be on the retracement side of market.
        if direction == "LONG":
            if entry > price * 1.000001:
                rejected.append(f"Ордер #{index}: LONG Entry выше текущей цены")
                continue
            risk, reward = entry - sl, tp - entry
        else:
            if entry < price * 0.999999:
                rejected.append(f"Ордер #{index}: SHORT Entry ниже текущей цены")
                continue
            risk, reward = sl - entry, entry - tp

        if risk <= 0 or reward <= 0:
            rejected.append(f"Ордер #{index}: SL/TP расположены с неправильной стороны")
            continue

        actual_rr = reward / risk
        if not isfinite(actual_rr) or actual_rr < min_reward_risk:
            rejected.append(
                f"Ордер #{index}: RR {actual_rr:.2f} ниже минимума {min_reward_risk:g}"
            )
            continue

        order = dict(raw)
        order["actual_rr"] = actual_rr
        order["entry_distance_pct"] = distance_pct
        accepted.append(order)

    if not accepted:
        return None, rejected or ["Нет ордеров, прошедших фильтр качества"]

    result = dict(signal)
    result["orders"] = accepted
    result["quality_filter"] = {
        "max_entry_distance_pct": float(max_entry_distance_pct),
        "min_reward_risk": float(min_reward_risk),
        "accepted_orders": len(accepted),
        "rejected_orders": len(rejected),
    }
    return result, rejected
