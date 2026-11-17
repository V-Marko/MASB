
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Optional, Sequence

import pandas as pd


# ============================================================
# MASB REGISTRY COMPATIBILITY
# ============================================================

NAME = "ob_trend"

INITIAL_CAPITAL = 100.0
MAX_ENTRIES = 1


# ============================================================
# CONFIGURATION
# ============================================================

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

    timeframe: str = "1h"


# ============================================================
# TRADE SIGNAL
# ============================================================

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


# ============================================================
# STRATEGY
# ============================================================

class OBTrendStrategy:

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

        if self.config.ema_fast <= 0 or self.config.ema_slow <= 0:
            raise ValueError("EMA должен быть больше нуля")

        if self.config.atr_period <= 0:
            raise ValueError("ATR должен быть больше нуля")

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # ORDER BLOCK
    # --------------------------------------------------------

    def _find_order_block(
        self,
        df: pd.DataFrame,
        side: str,
    ) -> Optional[dict]:

        cfg = self.config

        last_index = len(df) - 1

        first_index = max(
            cfg.swing_lookback + 2,
            last_index - cfg.ob_search_bars,
        )

        for i in range(
            last_index - 1,
            first_index - 1,
            -1,
        ):

            candle = df.iloc[i]
            following = df.iloc[i + 1]

            if pd.isna(candle["atr"]):
                continue

            if candle["atr"] <= 0:
                continue

            body = abs(
                candle["close"] - candle["open"]
            )

            displacement = (
                following["close"] - candle["close"]
                if side == "LONG"
                else candle["close"] - following["close"]
            )

            if displacement < (
                cfg.displacement_atr * candle["atr"]
            ):
                continue

            start = max(
                0,
                i - cfg.swing_lookback,
            )

            previous = df.iloc[start:i]

            if len(previous) < cfg.swing_lookback:
                continue

            if side == "LONG":

                # Bullish OB: последняя медвежья свеча.

                if candle["close"] >= candle["open"]:
                    continue

                # Подтверждённый пробой максимума.

                if following["close"] <= previous["high"].max():
                    continue

            else:

                # Bearish OB: последняя бычья свеча.

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

    # --------------------------------------------------------
    # GENERATE SIGNAL
    # --------------------------------------------------------

    def generate_signal(
        self,
        candles: pd.DataFrame,
        balance: Optional[float] = None,
    ) -> Optional[TradeSignal]:

        cfg = self.config

        df = candles.copy()

        df.columns = [
            str(column).lower()
            for column in df.columns
        ]

        required = {
            "open",
            "high",
            "low",
            "close",
            "volume",
        }

        if not required.issubset(df.columns):
            raise ValueError(
                f"Нужны столбцы: {sorted(required)}"
            )

        df = df[
            ["open", "high", "low", "close", "volume"]
        ].apply(
            pd.to_numeric,
            errors="coerce",
        )

        df = df.dropna().sort_index()

        minimum = max(
            cfg.ema_slow + 5,
            220,
        )

        if len(df) < minimum:
            return None

        # EMA 50

        df["ema_fast"] = df["close"].ewm(
            span=cfg.ema_fast,
            adjust=False,
        ).mean()

        # EMA 200

        df["ema_slow"] = df["close"].ewm(
            span=cfg.ema_slow,
            adjust=False,
        ).mean()

        # ATR

        df["atr"] = self._atr(
            df,
            cfg.atr_period,
        )

        last = df.iloc[-1]

        if pd.isna(last["atr"]) or last["atr"] <= 0:
            return None

        # Определяем тренд.

        trend = self._trend(df)

        if trend == "NEUTRAL":
            return None

        side = trend

        # Ищем подтверждённый OB.

        ob = self._find_order_block(
            df,
            side,
        )

        if ob is None:
            return None

        # Вход по закрытию свечи.

        entry = float(last["close"])

        # Проверяем повторное касание OB.

        touched = (
            last["low"] <= ob["high"]
            and last["high"] >= ob["low"]
        )

        if not touched:
            return None

        # Проверяем границы OB.

        if side == "LONG" and entry < ob["low"]:
            return None

        if side == "SHORT" and entry > ob["high"]:
            return None

        # Stop Loss и Take Profit.

        atr_buffer = (
            float(last["atr"]) * cfg.sl_atr_buffer
        )

        if side == "LONG":

            stop = ob["low"] - atr_buffer

            distance = entry - stop

            target = (
                entry + distance * cfg.reward_risk
            )

        else:

            stop = ob["high"] + atr_buffer

            distance = stop - entry

            target = (
                entry - distance * cfg.reward_risk
            )

        if distance <= 0:
            return None

        # Баланс.

        account_balance = (
            cfg.initial_balance
            if balance is None
            else float(balance)
        )

        if account_balance <= 0:
            return None

        # Риск 1%.

        risk_usdt = (
            account_balance * cfg.risk_pct / 100.0
        )

        # Количество по риску.

        quantity_by_risk = (
            risk_usdt / distance
        )

        # Ограничение плеча.

        leverage = min(
            cfg.leverage,
            cfg.max_leverage,
        )

        max_notional = (
            account_balance * leverage
        )

        quantity_by_margin = (
            max_notional / entry
        )

        quantity = min(
            quantity_by_risk,
            quantity_by_margin,
        )

        if quantity <= 0:
            return None

        notional = quantity * entry

        actual_risk = quantity * distance

        return TradeSignal(
            side=side,
            entry=entry,
            stop_loss=float(stop),
            take_profit=float(target),
            risk_usdt=float(actual_risk),
            quantity=float(quantity),
            notional=float(notional),
            leverage=float(leverage),
            order_block_low=ob["low"],
            order_block_high=ob["high"],
            trend=trend,
            reason=(
                f"{side}: EMA trend + confirmed OB retest; "
                f"RR 1:{cfg.reward_risk:g}"
            ),
        )


# ============================================================
# MASB COMPATIBILITY: ANALYZE()
# ============================================================

_strategy = OBTrendStrategy()


def analyze(
    candles: Sequence[dict[str, Any]],
    current_price: float | None = None,
    capital: float = INITIAL_CAPITAL,
    open_positions: int = 0,
    pending_orders: int = 0,
) -> dict[str, Any] | None:

    # Не создаём новые сигналы, если уже есть сделка.

    if open_positions + pending_orders >= MAX_ENTRIES:
        return None

    if not candles:
        return None

    # Конвертация свечей в DataFrame.

    df = pd.DataFrame(candles)

    df.columns = [
        str(column).lower()
        for column in df.columns
    ]

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            unit="ms",
            errors="coerce",
        )

        df = df.set_index("timestamp")

    elif "time" in df.columns:
        df = df.set_index("time")

    df = df.sort_index()

    signal = _strategy.generate_signal(
        candles=df,
        balance=capital,
    )

    if signal is None:
        return None

    order = {
        "entry_number": 1,
        "direction": signal.side,
        "side": signal.side,

        "entry": signal.entry,
        "tp": signal.take_profit,
        "sl": signal.stop_loss,

        "take_profit": signal.take_profit,
        "stop_loss": signal.stop_loss,

        "quantity": signal.quantity,
        "position_value": signal.notional,
        "notional": signal.notional,

        "risk_usdt": signal.risk_usdt,
        "leverage": signal.leverage,

        "status": "SIGNAL",
        "order_type": "MARKET",

        "order_block_low": signal.order_block_low,
        "order_block_high": signal.order_block_high,

        "rr": _strategy.config.reward_risk,
        "estimated_entry_fee": (
            signal.notional * _strategy.config.fee_rate
        ),
    }

    result = {
        "strategy": NAME,
        "direction": signal.side,
        "side": signal.side,

        "current_price": signal.entry,
        "capital": float(capital),

        "initial_capital": INITIAL_CAPITAL,
        "risk_pct": _strategy.config.risk_pct,

        "rr": _strategy.config.reward_risk,
        "commission_per_side": _strategy.config.fee_rate,

        "leverage": signal.leverage,
        "max_entries": MAX_ENTRIES,

        "trend": signal.trend,

        "order_block": {
            "low": signal.order_block_low,
            "high": signal.order_block_high,
        },

        "orders": [order],

        "signal": signal.to_dict(),
        "reason": signal.reason,
    }

    return result


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(
    signal: dict[str, Any] | None,
) -> str:

    if signal is None:
        return (
            "📦 OB Trend\n\n"
            "Нет нового подходящего сетапа.\n"
            "Ожидается подтверждённый OB и повторное касание."
        )

    lines = [
        "📦 MASB — OB Trend",
        "",
        f"Направление: {signal['direction']}",
        f"Тренд: {signal['trend']}",
        f"Текущая цена: {signal['current_price']:.8f}",
        f"RR: 1:{signal['rr']}",
        "",
        "Order Block:",
        f"  Low: {signal['order_block']['low']:.8f}",
        f"  High: {signal['order_block']['high']:.8f}",
        "",
    ]

    for order in signal["orders"]:

        lines.extend([
            "Сделка №1",
            f"  Тип: {order['order_type']}",
            f"  Entry: {order['entry']:.8f}",
            f"  TP: {order['tp']:.8f}",
            f"  SL: {order['sl']:.8f}",
            f"  Риск: {order['risk_usdt']:.4f} USDT",
            f"  Плечо: {order['leverage']}x",
            f"  Номинал: {order['notional']:.2f} USDT",
            f"  Количество: {order['quantity']:.8f}",
            f"  Комиссия входа: {order['estimated_entry_fee']:.4f} USDT",
            "",
        ])

    lines.append(signal["reason"])

    return "\n".join(lines)


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print(f"Strategy: {NAME}")
    print("Strategy: Order Block + EMA 50/200 + ATR")
    print(f"Initial balance: {INITIAL_CAPITAL} USDT")
    print(f"Risk: {_strategy.config.risk_pct}%")
    print(f"Leverage: {_strategy.config.leverage}x")
    print(f"RR: 1:{_strategy.config.reward_risk}")
    print(f"Timeframe: {_strategy.config.timeframe}")
    print("analyze(): AVAILABLE")
    print("Strategy loaded successfully.")
