
from __future__ import annotations

import argparse
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from services.binance_market import BinanceMarketError, get_klines  # noqa: E402
from services.strategy_manager import get_symbols, list_strategies  # noqa: E402
from strategy.registry import discover_strategies  # noqa: E402

try:
    from services.trade_monitor import active_trades  # noqa: E402
except Exception:  # pragma: no cover
    active_trades = None

CAPITAL = 100.0
WINDOW = 500          # столько свечей видит стратегия в боте
HOUR_MS = 3_600_000


def ts(ms: int | None) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%d.%m %H:%M UTC")


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def check_pipeline(only: str | None, symbols: list[str]) -> list[str]:
    section("1. СОСТОЯНИЕ БОТА")
    analyzers, _, _ = discover_strategies()
    print("Загружено стратегий в реестре:", ", ".join(analyzers) or "НИ ОДНОЙ")

    states = list_strategies()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    expected = (now_ms // HOUR_MS) * HOUR_MS - HOUR_MS
    print(f"Монет в списке: {len(symbols)}. Ожидаемая последняя закрытая свеча: {ts(expected)}")

    enabled: list[str] = []
    for name, info in states.items():
        if only and name != only:
            continue
        on = bool(info.get("enabled"))
        loaded = name in analyzers
        last = info.get("last_candle", {}) or {}
        fresh = sum(1 for s in symbols if last.get(s) == expected)
        print(f"\n• {name}: {'ВКЛ' if on else 'выкл'} | в реестре: {'да' if loaded else 'НЕТ !!!'} "
              f"| сигналов: {info.get('signals', 0)} | свеж. монет: {fresh}/{len(symbols)}")
        if info.get("last_error"):
            print(f"  последняя ошибка: {info['last_error']}")
        if on and not loaded:
            print("  !!! Стратегия включена, но файла/NAME нет в strategy/ — бот её молча ПРОПУСКАЕТ.")
        if on and loaded and fresh == 0:
            print("  !!! last_candle не обновляется — сканер до этой стратегии не доходит или падает.")
        if on and loaded:
            enabled.append(name)

    if active_trades is not None:
        try:
            trades = active_trades()
            counts = Counter((t.get("strategy"), t.get("symbol")) for t in trades)
            print(f"\nАктивных/ожидающих сделок: {len(trades)}")
            for (strat, sym), n in counts.most_common(15):
                print(f"  {strat} / {sym}: {n}  (пока она есть, стратегия по этой монете новых сигналов НЕ даёт)")
        except Exception as exc:
            print("Не удалось прочитать сделки:", exc)
    return enabled


def replay(name: str, analyzer, symbols: list[str], days: int) -> None:
    section(f"2. ИСТОРИЯ: {name} за {days} дн.")
    bars = days * 24
    total = 0
    errors: Counter[str] = Counter()
    per_symbol: dict[str, list[int]] = {}
    started = time.time()

    for symbol in symbols:
        try:
            candles = get_klines(symbol, min(1500, WINDOW + bars + 5))
        except (BinanceMarketError, ValueError) as exc:
            print(f"  {symbol}: нет данных ({exc})")
            continue
        if len(candles) < 60:
            print(f"  {symbol}: мало свечей ({len(candles)})")
            continue

        hits: list[int] = []
        start = max(40, len(candles) - bars)
        for end in range(start, len(candles) + 1):
            window = candles[max(0, end - WINDOW):end]
            try:
                signal = analyzer(
                    window,
                    current_price=float(window[-1]["close"]),
                    capital=CAPITAL,
                    open_positions=0,
                    pending_orders=0,
                )
            except Exception as exc:
                errors[f"{type(exc).__name__}: {exc}"] += 1
                if errors[f"{type(exc).__name__}: {exc}"] == 1:
                    traceback.print_exc()
                continue
            if signal and signal.get("orders"):
                hits.append(int(window[-1]["timestamp"]))
        per_symbol[symbol] = hits
        total += len(hits)
        mark = f"{len(hits)} сигн." + (f" (последний {ts(hits[-1])})" if hits else "")
        print(f"  {symbol:<14} {mark}")

    print(f"\nИТОГО {name}: {total} сигналов за {days} дн. на {len(per_symbol)} монетах "
          f"({time.time() - started:.0f} с)")
    if errors:
        print("ОШИБКИ внутри стратегии (из-за них сигналов может не быть):")
        for msg, n in errors.most_common(5):
            print(f"  x{n}: {msg}")
    if total == 0 and not errors:
        print("→ Стратегия отработала без ошибок, но условия входа за этот период не выполнялись ни разу.")
    elif total > 0:
        print("→ Стратегия СПОСОБНА давать сигналы: если бот их не присылает, проблема в сканере/отправке.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--only", help="проверить только эту стратегию")
    parser.add_argument("--symbols", nargs="*", help="список монет (по умолчанию из бота)")
    args = parser.parse_args()

    symbols = [s.upper() for s in args.symbols] if args.symbols else get_symbols()
    enabled = check_pipeline(args.only, symbols)
    analyzers, _, _ = discover_strategies()
    for name in enabled:
        replay(name, analyzers[name], symbols, args.days)
    print("\nГотово. Скопируйте весь вывод и отправьте мне.")


if __name__ == "__main__":
    main()
