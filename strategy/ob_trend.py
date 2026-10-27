
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import pandas as pd


@dataclass
class StrategyConfig:
    initial_balance: float = 100.0
    risk_pct: float = 1.0
    reward_risk: float = 3.0

    ema_fast: int = 50
    ema_slow: int = 200
    atr_period: int = 14

    swing_lookback: int = 5
    ob_search_bars: int = 12
    displacement_atr: float = 1.0
    sl_atr_buffer: float = 0.15

    leverage: float = 5.0
    max_leverage: float = 5.0
    fee_rate: float = 0.00055

    # Позиция открывается при повторном касании OB.
    # Никаких сигналов по незакрытой свече.
    timeframe: str = "1h"


@dataclass
class TradeSignal:
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    risk_usdt: float
    quantity: float
    notional: float
    leverage: float
    order_block_low: float
    order_block_high: float
    trend: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


class OBTrendStrategy:
    """
    Order Block + EMA 50/200 + ATR.
    Вход только по направлению тренда.

    Входные данные:
        DataFrame со столбцами open, high, low, close, volume.
        Индекс должен быть отсортирован по времени.
        Последняя строка считается закрытой свечой.

    Этот модуль формирует сигналы и не отправляет ордера на биржу.
    """

    def __init__(
        self,
        config: Optional[StrategyConfig] = None,
    ):
        self.config = config or StrategyConfig()

        if self.config.risk_pct <= 0:
            raise ValueError("risk_pct должен быть больше нуля")

        if self.config.reward_risk <= 0:
            raise ValueError("reward_risk должен быть больше нуля")

        if self.config.max_leverage <= 0:
            raise ValueError("max_leverage должен быть больше нуля")

        if self.config.leverage <= 0:
            raise ValueError("leverage должен быть больше нуля")

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        previous_close = df["close"].shift(1)

        true_range = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - previous_close).abs(),
                (df["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        return true_range.ewm(
            alpha=1 / period,
            adjust=False,
            min_periods=period,
        ).mean()

    def _trend(self, df: pd.DataFrame) -> str:
        last = df.iloc[-1]

        bullish = (
            last["close"] > last["ema_slow"]
            and last["ema_fast"] > last["ema_slow"]
            and last["ema_fast"] > df["ema_fast"].iloc[-2]
        )

        bearish = (
            last["close"] < last["ema_slow"]
            and last["ema_fast"] < last["ema_slow"]
            and last["ema_fast"] < df["ema_fast"].iloc[-2]
        )

        if bullish:
            return "LONG"

        if bearish:
            return "SHORT"

        return "NEUTRAL"

    def _find_order_block(
        self,
        df: pd.DataFrame,
        side: str,
    ) -> Optional[dict]:
        """
        Ищет последний подтверждённый OB.

        Bullish OB:
        Последняя медвежья свеча перед сильным импульсом вверх,
        который пробивает предыдущий локальный максимум.

        Bearish OB:
        Последняя бычья свеча перед сильным импульсом вниз,
        который пробивает предыдущий локальный минимум.

        Проверяются только свечи, закрытые до текущей.
        """

        cfg = self.config

        # Текущая свеча используется только для входа.
        # OB должен быть сформирован ранее.
        last_index = len(df) - 1
        first_index = max(
            cfg.swing_lookback + 2,
            last_index - cfg.ob_search_bars,
        )

        for i in range(last_index - 1, first_index - 1, -1):
            candle = df.iloc[i]
            following = df.iloc[i + 1]

            if pd.isna(candle["atr"]):
                continue

            if candle["atr"] <= 0:
                continue

            body = abs(candle["close"] - candle["open"])

            # Проверяем, что после свечи возник импульс.
            displacement = (
                following["close"] - candle["close"]
                if side == "LONG"
                else candle["close"] - following["close"]
            )

            if displacement < cfg.displacement_atr * candle["atr"]:
                continue

            start = max(0, i - cfg.swing_lookback)
            previous = df.iloc[start:i]

            if len(previous) < cfg.swing_lookback:
                continue

            if side == "LONG":
                # Последняя красная свеча перед импульсом вверх.
                if candle["close"] >= candle["open"]:
                    continue

                # Подтверждённый пробой максимума.
                if following["close"] <= previous["high"].max():
                    continue

            else:
                # Последняя зелёная свеча перед импульсом вниз.
                if candle["close"] <= candle["open"]:
                    continue

                # Подтверждённый пробой минимума.
                if following["close"] >= previous["low"].min():
                    continue

            return {
                "side": side,
                "low": float(candle["low"]),
                "high": float(candle["high"]),
                "index": i,
                "time": df.index[i],
                "body": float(body),
            }

        return None

    def generate_signal(
        self,
        candles: pd.DataFrame,
        balance: Optional[float] = None,
    ) -> Optional[TradeSignal]:
        """
        Возвращает TradeSignal или None.

        Важно: вызывай метод после закрытия часовой свечи.
        Повторное открытие позиции контролируется отдельно в MASB.
        """

        cfg = self.config

        required = {"open", "high", "low", "close", "volume"}

        if not required.issubset(candles.columns):
            raise ValueError(
                f"Нужны столбцы: {sorted(required)}"
            )

        df = candles.copy()
        df.columns = [str(c).lower() for c in df.columns]

        df = df.sort_index()

        df = df[
            ["open", "high", "low", "close", "volume"]
        ].apply(pd.to_numeric, errors="coerce")

        df = df.dropna()

        minimum = max(cfg.ema_slow + 5, 220)

        if len(df) < minimum:
            return None

        # Индикаторы рассчитываются только по текущим
        # и предыдущим данным, без lookahead.
        df["ema_fast"] = df["close"].ewm(
            span=cfg.ema_fast,
            adjust=False,
        ).mean()

        df["ema_slow"] = df["close"].ewm(
            span=cfg.ema_slow,
            adjust=False,
        ).mean()

        df["atr"] = self._atr(df, cfg.atr_period)

        last = df.iloc[-1]

        if pd.isna(last["atr"]) or last["atr"] <= 0:
            return None

        trend = self._trend(df)

        # В боковике сделок нет.
        if trend == "NEUTRAL":
            return None

        # Направление OB всегда соответствует тренду.
        side = trend
        ob = self._find_order_block(df, side)

        if ob is None:
            return None

        entry = float(last["close"])

        # Не входим, если цена ещё не вернулась в зону OB.
        touched = (
            last["low"] <= ob["high"]
            and last["high"] >= ob["low"]
        )

        if not touched:
            return None

        # Не открываем позицию, если закрытие уже
        # вышло за противоположную границу OB.
        if side == "LONG" and entry < ob["low"]:
            return None

        if side == "SHORT" and entry > ob["high"]:
            return None

        atr_buffer = float(last["atr"]) * cfg.sl_atr_buffer

        if side == "LONG":
            stop = ob["low"] - atr_buffer
            distance = entry - stop
            target = entry + distance * cfg.reward_risk
        else:
            stop = ob["high"] + atr_buffer
            distance = stop - entry
            target = entry - distance * cfg.reward_risk

        if distance <= 0:
            return None

        account_balance = (
            cfg.initial_balance if balance is None else balance
        )

        if account_balance <= 0:
            return None

        # Риск рассчитывается по расстоянию до SL.
        risk_usdt = account_balance * cfg.risk_pct / 100.0

        quantity_by_risk = risk_usdt / distance

        leverage = min(cfg.leverage, cfg.max_leverage)

        max_notional = account_balance * leverage
        quantity_by_margin = max_notional / entry

        # Ограничиваем позицию доступной маржой.
        quantity = min(quantity_by_risk, quantity_by_margin)

        if quantity <= 0:
            return None

        notional = quantity * entry

        return TradeSignal(
            side=side,
            entry=entry,
            stop_loss=stop,
            take_profit=target,
            risk_usdt=quantity * distance,
            quantity=quantity,
            notional=notional,
            leverage=leverage,
            order_block_low=ob["low"],
            order_block_high=ob["high"],
            trend=trend,
            reason=(
                f"{side}: EMA trend + confirmed OB retest; "
                f"RR 1:{cfg.reward_risk:g}"
            ),
        )
