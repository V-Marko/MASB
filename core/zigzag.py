
from dataclasses import dataclass
from typing import Any, Literal, Sequence


@dataclass(frozen=True)
class Swing:
    index: int
    price: float
    kind: Literal["LOW", "HIGH"]
    confirmed_at: int


def find_confirmed_swings(
    candles: Sequence[dict[str, Any]],
    deviation_percent: float = 10.0,
) -> list[Swing]:


    if not 0 < deviation_percent < 100:
        raise ValueError(
            "deviation_percent должен быть больше 0 и меньше 100."
        )

    if len(candles) < 2:
        return []

    deviation = deviation_percent / 100.0

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    if any(price <= 0 for price in highs + lows):
        raise ValueError("Цены High и Low должны быть положительными.")

    if any(highs[i] < lows[i] for i in range(len(candles))):
        raise ValueError("High не может быть меньше Low.")

    swings: list[Swing] = []

    # Начальное направление устанавливается после достижения
    # заданного отклонения от первого наблюдаемого экстремума.
    candidate_high = highs[0]
    candidate_high_index = 0

    candidate_low = lows[0]
    candidate_low_index = 0

    direction = 0  # 0 = не определено, 1 = вверх, -1 = вниз

    for i in range(1, len(candles)):
        high = highs[i]
        low = lows[i]

        if direction == 0:
            if high > candidate_high:
                candidate_high = high
                candidate_high_index = i

            if low < candidate_low:
                candidate_low = low
                candidate_low_index = i

            # Сначала проверяем, какой разворот подтвердился.
            if low <= candidate_high * (1 - deviation):
                swings.append(
                    Swing(
                        index=candidate_high_index,
                        price=candidate_high,
                        kind="HIGH",
                        confirmed_at=i,
                    )
                )
                direction = -1
                candidate_low = low
                candidate_low_index = i

            elif high >= candidate_low * (1 + deviation):
                swings.append(
                    Swing(
                        index=candidate_low_index,
                        price=candidate_low,
                        kind="LOW",
                        confirmed_at=i,
                    )
                )
                direction = 1
                candidate_high = high
                candidate_high_index = i

            continue

        if direction == 1:
            # Ищем максимум восходящего участка.
            if high > candidate_high:
                candidate_high = high
                candidate_high_index = i

            # Максимум подтверждён движением вниз на заданный процент.
            if low <= candidate_high * (1 - deviation):
                swings.append(
                    Swing(
                        index=candidate_high_index,
                        price=candidate_high,
                        kind="HIGH",
                        confirmed_at=i,
                    )
                )
                direction = -1
                candidate_low = low
                candidate_low_index = i

        else:
            # Ищем минимум нисходящего участка.
            if low < candidate_low:
                candidate_low = low
                candidate_low_index = i

            # Минимум подтверждён движением вверх на заданный процент.
            if high >= candidate_low * (1 + deviation):
                swings.append(
                    Swing(
                        index=candidate_low_index,
                        price=candidate_low,
                        kind="LOW",
                        confirmed_at=i,
                    )
                )
                direction = 1
                candidate_high = high
                candidate_high_index = i

    return swings
