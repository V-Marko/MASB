from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


BASE_URL = "https://fapi.binance.com"
INTERVAL = "1h"
LIMIT = 500


class BinanceMarketError(RuntimeError):
    pass


def get_klines(
    symbol: str,
    limit: int = LIMIT,
    *,
    include_current: bool = False,
) -> list[dict[str, Any]]:
    symbol = symbol.upper().strip()

    if not symbol.endswith("USDT"):
        raise ValueError(f"Поддерживаются USDT-пары, получено: {symbol}")

    params = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": INTERVAL,
        "limit": max(10, min(int(limit), 1500)),
    })

    request = urllib.request.Request(
        f"{BASE_URL}/fapi/v1/klines?{params}",
        headers={"User-Agent": "MASB/1.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise BinanceMarketError(
            f"Не удалось загрузить свечи {symbol}: {exc}"
        ) from exc

    if not isinstance(payload, list):
        raise BinanceMarketError(f"Binance вернул ошибку для {symbol}: {payload}")

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    candles = []

    for row in payload:
        if len(row) < 7:
            continue

        # Для поиска новых сигналов используем только закрытые свечи.
        # Монитор сделок может запросить текущую свечу, чтобы замечать касания
        # Entry/TP/SL внутри часа.
        if not include_current and int(row[6]) >= now_ms:
            continue

        candles.append({
            "timestamp": int(row[0]),
            "time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })

    return candles