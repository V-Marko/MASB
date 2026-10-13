
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
    balance: float,
    risk_percent: float,
    entry: float,
    sl: float,
) -> dict[str, float]:

    balance = float(balance)
    risk_percent = float(risk_percent)
    entry = float(entry)
    sl = float(sl)

    if balance <= 0:
        raise ValueError("Баланс должен быть больше нуля.")

    if not 0 < risk_percent <= 100:
        raise ValueError("Риск должен быть в диапазоне (0, 100].")

    if entry <= 0 or sl <= 0:
        raise ValueError("Entry и SL должны быть больше нуля.")

    if entry == sl:
        raise ValueError("Entry и SL не должны совпадать.")

    risk_amount = balance * risk_percent / 100.0
    price_distance_percent = abs(entry - sl) / entry

    # Приблизительный номинал позиции:
    # допустимый убыток / относительное расстояние до SL.
    position_value = risk_amount / price_distance_percent
    quantity = position_value / entry

    return {
        "balance": balance,
        "risk_percent": risk_percent,
        "risk_amount": risk_amount,
        "position_value": position_value,
        "quantity": quantity,
    }
