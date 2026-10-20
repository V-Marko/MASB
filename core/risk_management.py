
from typing import Literal


Direction = Literal["LONG", "SHORT"]


def calculate_sl(
    entry: float,
    tp: float,
    rr: float = 3.0,
    direction: Direction = "LONG",
) -> float:


    entry = float(entry)
    tp = float(tp)
    rr = float(rr)

    if entry <= 0:
        raise ValueError("Entry должен быть больше нуля.")

    if tp <= 0:
        raise ValueError("TP должен быть больше нуля.")

    if rr <= 0:
        raise ValueError("RR должен быть больше нуля.")

    if direction == "LONG":
        if tp <= entry:
            raise ValueError("Для LONG TP должен быть выше Entry.")

        reward = tp - entry
        return entry - reward / rr

    if direction == "SHORT":
        if tp >= entry:
            raise ValueError("Для SHORT TP должен быть ниже Entry.")

        reward = entry - tp
        return entry + reward / rr

    raise ValueError("direction должен быть LONG или SHORT.")


def calculate_position_size(
    balance,
    entry,
    sl,
    risk_percent=1.0,
    leverage=10
):
    if balance <= 0 or entry <= 0:
        return None

    if not 0 < risk_percent <= 100:
        return None

    if leverage <= 0:
        return None

    # 1. Рассчитываем маржу
    margin = balance * risk_percent / 100.0

    # 2. Рассчитываем номинальную стоимость позиции
    position_value = margin * leverage

    # 3. Рассчитываем количество монет
    quantity = position_value / entry

    # 4. Рассчитываем расстояние до Stop Loss
    sl_distance_percent = abs(entry - sl) / entry * 100

    # 5. Оцениваем убыток при достижении SL
    potential_loss = quantity * abs(entry - sl)

    # 6. Рассчитываем Take Profit для RR 1:3
    # Направление определяется по расположению SL
    if sl < entry:
        tp = entry + (entry - sl) * 3
    elif sl > entry:
        tp = entry - (sl - entry) * 3
    else:
        return None

    return {
        "margin": margin,
        "leverage": leverage,
        "position_value": position_value,
        "quantity": quantity,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": 3.0,
        "sl_distance_percent": sl_distance_percent,
        "potential_loss": potential_loss,
        "potential_profit": potential_loss * 3
    }
