
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Any

import websockets

logger = logging.getLogger("MASB.Scalper")

WS_URL = "wss://fstream.binance.com/stream"

# Настройки скальпера
SYMBOLS = [
    "btcusdt",
    "ethusdt",
    "solusdt",
    "bnbusdt",
    "xrpusdt",
    "dogeusdt",
    "suiusdt",
    "linkusdt",
]

OBI_THRESHOLD = 0.25
MIN_DELTA_RATIO = 0.10
VOLUME_MULTIPLIER = 1.20
EMA_FAST = 9
EMA_SLOW = 21
STOP_ATR_MULTIPLIER = 1.5
RISK_REWARD = 2.0
RISK_PERCENT = 0.5
MAX_LEVERAGE = 5
MAX_POSITIONS = 3
SIGNAL_COOLDOWN = 300
ATR_PERIOD = 14


@dataclass
class MarketState:
    bids_value: float = 0.0
    asks_value: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    trade_volume: float = 0.0
    last_price: float = 0.0
    last_trade_time: float = 0.0
    last_signal_time: float = 0.0
    prices: deque | None = None
    volumes: deque | None = None
    highs: deque | None = None
    lows: deque | None = None
    closes: deque | None = None
    candle_volume: deque | None = None

    def __post_init__(self):
        self.prices = deque(maxlen=100)
        self.volumes = deque(maxlen=100)
        self.highs = deque(maxlen=100)
        self.lows = deque(maxlen=100)
        self.closes = deque(maxlen=100)
        self.candle_volume = deque(maxlen=100)


class ScalperEngine:
    """
    MASB market scanner.

    Сканирует Binance USDⓈ-M Futures:
    - order book imbalance;
    - поток агрессивных покупок и продаж;
    - EMA 9/21;
    - ATR для расчёта SL;
    - виртуальные позиции и риск.

    callback(signal) вызывается при новом сигнале.
    Реальные ордера на биржу не отправляются.
    """

    def __init__(
        self,
        callback: Callable[[dict[str, Any]], Any],
        symbols: list[str] | None = None,
        initial_balance: float = 100.0,
    ):
        self.callback = callback
        self.symbols = [
            s.lower().strip() for s in (symbols or SYMBOLS)
        ]
        self.balance = float(initial_balance)
        self.start_balance = float(initial_balance)
        self.states = {s: MarketState() for s in self.symbols}
        self.positions: dict[str, dict[str, Any]] = {}
        self.running = False
        self.day_start = time.time()
        self.day_start_balance = self.balance
        self.daily_loss_limit = 0.025
        self.max_drawdown = 0.10

    async def _emit(self, payload: dict[str, Any]):
        try:
            result = self.callback(payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("Ошибка callback скальпера")

    @staticmethod
    def _ema(values: list[float], period: int) -> float | None:
        if len(values) < period:
            return None

        alpha = 2.0 / (period + 1.0)
        result = values[0]

        for value in values[1:]:
            result = alpha * value + (1 - alpha) * result

        return result

    @staticmethod
    def _atr(state: MarketState) -> float | None:
        highs = list(state.highs or [])
        lows = list(state.lows or [])
        closes = list(state.closes or [])

        if len(closes) < ATR_PERIOD + 1:
            return None

        ranges = []

        for i in range(1, len(closes)):
            ranges.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )

        recent = ranges[-ATR_PERIOD:]
        return sum(recent) / len(recent) if recent else None

    @staticmethod
    def _imbalance(bids: float, asks: float) -> float:
        total = bids + asks
        return (bids - asks) / total if total > 0 else 0.0

    def _trend(self, state: MarketState) -> str | None:
        closes = list(state.closes or [])

        fast = self._ema(closes[-60:], EMA_FAST)
        slow = self._ema(closes[-60:], EMA_SLOW)

        if fast is None or slow is None:
            return None

        if fast > slow:
            return "LONG"
        if fast < slow:
            return "SHORT"
        return None

    def _delta_ratio(self, state: MarketState) -> float:
        total = state.buy_volume + state.sell_volume

        if total <= 0:
            return 0.0

        return (state.buy_volume - state.sell_volume) / total

    def _volume_ok(self, state: MarketState) -> bool:
        volumes = list(state.candle_volume or [])

        if len(volumes) < 21:
            return False

        previous = volumes[-21:-1]
        average = sum(previous) / len(previous)

        return (
            average > 0
            and volumes[-1] >= average * VOLUME_MULTIPLIER
        )

    def _risk_allowed(self) -> bool:
        if len(self.positions) >= MAX_POSITIONS:
            return False

        if self.balance <= 0:
            return False

        if time.time() - self.day_start >= 86400:
            self.day_start = time.time()
            self.day_start_balance = self.balance

        if self.day_start_balance > 0:
            daily_loss = (
                self.day_start_balance - self.balance
            ) / self.day_start_balance

            if daily_loss >= self.daily_loss_limit:
                return False

        drawdown = (
            self.start_balance - self.balance
        ) / self.start_balance

        return drawdown < self.max_drawdown

    def _make_signal(
        self,
        symbol: str,
        state: MarketState,
    ) -> dict[str, Any] | None:
        if symbol in self.positions:
            return None

        if time.time() - state.last_signal_time < SIGNAL_COOLDOWN:
            return None

        if not self._risk_allowed():
            return None

        price = state.last_price
        if price <= 0:
            return None

        trend = self._trend(state)
        atr = self._atr(state)

        if trend is None or atr is None or atr <= 0:
            return None

        imbalance = self._imbalance(
            state.bids_value,
            state.asks_value,
        )
        delta = self._delta_ratio(state)

        if not self._volume_ok(state):
            return None

        # Не торгуем против EMA-тренда.
        if trend == "LONG":
            if imbalance < OBI_THRESHOLD:
                return None
            if delta < MIN_DELTA_RATIO:
                return None
            side = "LONG"
            sl = price - atr * STOP_ATR_MULTIPLIER
            tp = price + (price - sl) * RISK_REWARD

        else:
            if imbalance > -OBI_THRESHOLD:
                return None
            if delta > -MIN_DELTA_RATIO:
                return None
            side = "SHORT"
            sl = price + atr * STOP_ATR_MULTIPLIER
            tp = price - (sl - price) * RISK_REWARD

        risk_amount = self.balance * RISK_PERCENT / 100
        stop_distance = abs(price - sl)

        if stop_distance <= 0:
            return None

        # Размер позиции рассчитывается по риску до SL.
        quantity = risk_amount / stop_distance
        notional = quantity * price

        # Ограничиваем номинал доступным плечом.
        max_notional = self.balance * MAX_LEVERAGE
        if notional > max_notional:
            notional = max_notional
            quantity = notional / price

        state.last_signal_time = time.time()

        return {
            "strategy": "scalperOBI",
            "symbol": symbol.upper(),
            "direction": side,
            "entry": price,
            "sl": sl,
            "tp": tp,
            "quantity": quantity,
            "position_value": notional,
            "risk_usdt": quantity * stop_distance,
            "risk_pct": RISK_PERCENT,
            "leverage": MAX_LEVERAGE,
            "rr": RISK_REWARD,
            "atr": atr,
            "obi": imbalance,
            "delta": delta,
            "trend": trend,
            "timestamp": int(time.time()),
            "mode": "PAPER",
        }

    async def _handle_message(self, message: dict[str, Any]):
        data = message.get("data", message)
        event = data.get("e")
        symbol = data.get("s", "").lower()

        if symbol not in self.states:
            return

        state = self.states[symbol]

        if event == "depthUpdate":
            bids = data.get("b", [])
            asks = data.get("a", [])

            state.bids_value = sum(
                float(price) * float(qty)
                for price, qty in bids[:20]
            )
            state.asks_value = sum(
                float(price) * float(qty)
                for price, qty in asks[:20]
            )

        elif event == "aggTrade":
            price = float(data["p"])
            quantity = float(data["q"])
            is_buyer_maker = data["m"]

            state.last_price = price
            state.last_trade_time = time.time()
            state.prices.append(price)
            state.trade_volume += quantity

            # m=True означает, что покупатель был maker:
            # агрессивная сторона — продавец.
            if is_buyer_maker:
                state.sell_volume += quantity
            else:
                state.buy_volume += quantity

            signal = self._make_signal(symbol, state)

            if signal:
                self.positions[symbol] = {
                    **signal,
                    "status": "OPEN",
                }
                await self._emit({
                    "type": "ENTRY",
                    **signal,
                })

    async def _monitor_positions(self):
        while self.running:
            for symbol, position in list(self.positions.items()):
                state = self.states[symbol]
                price = state.last_price

                if price <= 0:
                    continue

                side = position["direction"]

                if side == "LONG":
                    reason = (
                        "SL" if price <= position["sl"]
                        else "TP" if price >= position["tp"]
                        else None
                    )
                else:
                    reason = (
                        "SL" if price >= position["sl"]
                        else "TP" if price <= position["tp"]
                        else None
                    )

                if reason is None:
                    continue

                entry = position["entry"]
                qty = position["quantity"]

                if side == "LONG":
                    gross_pnl = (price - entry) * qty
                else:
                    gross_pnl = (entry - price) * qty

                # Оценка комиссии на вход и выход.
                fee = (
                    entry * qty + price * qty
                ) * 0.00055

                pnl = gross_pnl - fee
                self.balance += pnl

                await self._emit({
                    "type": "EXIT",
                    "strategy": "scalperOBI",
                    "symbol": symbol.upper(),
                    "direction": side,
                    "entry": entry,
                    "exit": price,
                    "reason": reason,
                    "pnl": pnl,
                    "balance": self.balance,
                    "mode": "PAPER",
                    "timestamp": int(time.time()),
                })

                del self.positions[symbol]

            await asyncio.sleep(0.25)

    async def run(self):
        self.running = True
        streams = []

        for symbol in self.symbols:
            streams.extend([
                f"{symbol}@depth20@100ms",
                f"{symbol}@aggTrade",
                f"{symbol}@kline_5m",
            ])

        url = WS_URL + "?streams=" + "/".join(streams)

        while self.running:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=2**20,
                ) as ws:
                    logger.info(
                        "MASB scalper connected: %s symbols",
                        len(self.symbols),
                    )

                    monitor = asyncio.create_task(
                        self._monitor_positions()
                    )

                    try:
                        async for raw in ws:
                            message = json.loads(raw)
                            data = message.get("data", {})
                            event = data.get("e")

                            # Обрабатываем закрытые 5m свечи.
                            if event == "kline":
                                kline = data.get("k", {})
                                if not kline.get("x"):
                                    continue

                                symbol = data.get("s", "").lower()
                                if symbol not in self.states:
                                    continue

                                state = self.states[symbol]
                                state.highs.append(float(kline["h"]))
                                state.lows.append(float(kline["l"]))
                                state.closes.append(float(kline["c"]))
                                state.candle_volume.append(
                                    float(kline["v"])
                                )
                                continue

                            await self._handle_message(message)

                    finally:
                        monitor.cancel()
                        await asyncio.gather(
                            monitor,
                            return_exceptions=True,
                        )

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "MASB scalper connection error; reconnecting"
                )
                await asyncio.sleep(5)

    def stop(self):
        self.running = False


