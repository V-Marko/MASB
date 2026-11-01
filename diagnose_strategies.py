from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

from collections import Counter
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from services.strategy_manager import get_symbols, list_strategies
from strategy.registry import discover_strategies

try:
    from services.trade_monitor import active_trades
except Exception:
    active_trades = None


# ============================================================
# SETTINGS
# ============================================================

CAPITAL = 100.0

DAYS = 1095
WINDOW = 500

FEE_RATE = 0.00055
POSITION_CAPITAL_PERCENT = 50.0

HOUR_MS = 3_600_000

BINANCE_URL = "https://fapi.binance.com/fapi/v1/klines"
REQUEST_LIMIT = 1500
PROGRESS_EVERY = 1000
CACHE_DIR = ROOT / "data" / "historical_cache"


# ============================================================
# LOG TO TXT
# ============================================================

LOG_DIR = ROOT / "logs"


class Tee:
    """Выводит в консоль как обычно и дублирует всё в txt файл.
    Прогресс-бары (\\r) в файле сохраняются одной итоговой строкой."""

    def __init__(self, console, file):
        self.console = console
        self.file = file
        self.buffer = ""

    def write(self, text):
        self.console.write(text)
        self.console.flush()

        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if "\r" in line:
                line = line.split("\r")[-1]
            self.file.write(line + "\n")
        self.file.flush()

    def flush(self):
        self.console.flush()
        self.file.flush()

    def isatty(self):
        return False


# ============================================================
# HELPERS
# ============================================================

def section(title: str) -> None:
    print("\n" + "=" * 75)
    print(title)
    print("=" * 75)


def ts(ms: int | None) -> str:
    if not ms:
        return "—"

    return datetime.fromtimestamp(
        ms / 1000,
        tz=timezone.utc
    ).strftime("%d.%m.%Y %H:%M UTC")


def number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def show_progress(
    label: str,
    current: int,
    total: int,
    width: int = 25
) -> None:

    total = max(total, 1)
    current = min(current, total)

    percent = current / total
    filled = int(width * percent)

    bar = "█" * filled + "░" * (width - filled)

    print(
        f"\r{label:<14} [{bar}] "
        f"{percent * 100:6.2f}% "
        f"({current:,}/{total:,})",
        end="",
        flush=True
    )

    if current >= total:
        print()


# ============================================================
# HISTORICAL BINANCE DATA
# ============================================================

def get_historical_klines(
    symbol: str,
    days: int
) -> list[dict]:
    """Load candles from disk cache and download only missing ranges.

    The first run downloads the available history once and saves it to JSON.
    Later runs reuse the cache, fill any missing hourly ranges, and append
    only newly closed candles. The caller then shares the returned list among
    every strategy, so Binance is not queried again per strategy.
    """

    now_ms = int(time.time() * 1000)
    last_closed_open = (now_ms // HOUR_MS) * HOUR_MS - HOUR_MS
    expected_candles = days * 24 + WINDOW
    target_start = last_closed_open - (expected_candles - 1) * HOUR_MS

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{symbol.upper()}_1h.json"

    candles_by_time: dict[int, dict] = {}

    # --------------------------------------------------------
    # 1. READ EXISTING CACHE
    # --------------------------------------------------------
    if cache_path.exists():
        try:
            with cache_path.open("r", encoding="utf-8") as file:
                cached_data = json.load(file)

            if isinstance(cached_data, list):
                for candle in cached_data:
                    timestamp = int(candle["timestamp"])
                    if target_start <= timestamp <= last_closed_open:
                        candles_by_time[timestamp] = candle

            print(
                f"\n{symbol}: CACHE HIT | "
                f"{len(candles_by_time):,} cached candles"
            )

        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            print(f"\n{symbol}: cache unreadable, rebuilding: {exc}")
            candles_by_time.clear()

    else:
        print(f"\n{symbol}: CACHE MISS | first historical download")

    # --------------------------------------------------------
    # 2. BUILD MISSING HOURLY RANGES
    # --------------------------------------------------------
    cached_times = sorted(candles_by_time)
    missing_ranges: list[tuple[int, int]] = []

    if not cached_times:
        missing_ranges.append((target_start, last_closed_open))
    else:
        # Fill history before the first cached candle. If the symbol was
        # listed later, Binance simply returns its earliest available data.
        if cached_times[0] > target_start:
            missing_ranges.append((target_start, cached_times[0] - HOUR_MS))

        # Fill holes inside the cached history.
        previous = cached_times[0]
        for current in cached_times[1:]:
            if current - previous > HOUR_MS:
                missing_ranges.append((previous + HOUR_MS, current - HOUR_MS))
            previous = current

        # Append candles newer than the newest cached candle.
        if cached_times[-1] < last_closed_open:
            missing_ranges.append((cached_times[-1] + HOUR_MS, last_closed_open))

    # --------------------------------------------------------
    # 3. DOWNLOAD ONLY THE MISSING RANGES
    # --------------------------------------------------------
    downloaded = 0

    def download_range(range_start: int, range_end: int) -> None:
        nonlocal downloaded

        cursor = range_start

        while cursor <= range_end:
            params = urllib.parse.urlencode({
                "symbol": symbol,
                "interval": "1h",
                "startTime": cursor,
                "endTime": range_end + HOUR_MS - 1,
                "limit": REQUEST_LIMIT,
            })

            request = urllib.request.Request(
                f"{BINANCE_URL}?{params}",
                headers={"User-Agent": "MASB-Historical-Backtest"},
            )

            data = None
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        data = json.loads(response.read().decode("utf-8"))
                    break
                except (
                    urllib.error.HTTPError,
                    urllib.error.URLError,
                    TimeoutError,
                ) as exc:
                    if attempt == 3:
                        raise RuntimeError(
                            f"Binance request failed for {symbol}: {exc}"
                        ) from exc
                    print(f"\n  {symbol}: retry {attempt + 1}/3: {exc}")
                    time.sleep(2 ** attempt)

            if not data:
                # No candles in this range can mean the symbol was not yet
                # listed. Do not loop forever over an empty response.
                break

            last_open_time = None
            for row in data:
                open_time = int(row[0])
                close_time = int(row[6])

                if close_time >= now_ms:
                    continue
                if open_time < range_start or open_time > range_end:
                    continue

                candle = {
                    "timestamp": open_time,
                    "time": open_time,
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }

                if open_time not in candles_by_time:
                    downloaded += 1

                candles_by_time[open_time] = candle
                last_open_time = open_time

            if last_open_time is None:
                break

            next_cursor = last_open_time + HOUR_MS
            if next_cursor <= cursor:
                break

            cursor = next_cursor

            show_progress(
                f"{symbol} download:",
                min(len(candles_by_time), expected_candles),
                expected_candles,
            )

            if len(data) < REQUEST_LIMIT:
                break

    for range_start, range_end in missing_ranges:
        if range_start <= range_end:
            download_range(range_start, range_end)

    # --------------------------------------------------------
    # 4. SORT AND SAVE THE UPDATED CACHE
    # --------------------------------------------------------
    candles = sorted(
        candles_by_time.values(),
        key=lambda candle: candle["timestamp"],
    )

    if len(candles) > expected_candles:
        candles = candles[-expected_candles:]

    if candles:
        temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(candles, file, separators=(",", ":"))
            temp_path.replace(cache_path)
        except OSError as exc:
            print(f"\n  WARNING: could not save candle cache: {exc}")
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass

        print(
            f"\n  {symbol}: READY | {len(candles):,} candles | "
            f"New downloaded: {downloaded:,}"
        )
        print(
            f"  From {ts(candles[0]['timestamp'])} "
            f"| To {ts(candles[-1]['timestamp'])}"
        )
        print(f"  Cache file: {cache_path}")
    else:
        print(f"\n  {symbol}: no historical data available.")

    return candles


# ============================================================
# BOT STATUS
# ============================================================

def check_pipeline(
    only: str | None,
    symbols: list[str]
) -> list[str]:

    section("1. BOT STATUS")

    analyzers, _, _ = discover_strategies()

    print(
        "Loaded strategies:",
        ", ".join(analyzers) or "NONE"
    )

    states = list_strategies()

    now_ms = int(time.time() * 1000)

    expected = (
        (now_ms // HOUR_MS) * HOUR_MS - HOUR_MS
    )

    print(f"Coins: {len(symbols)}")
    print(
        f"Expected last closed candle: {ts(expected)}"
    )

    enabled = []

    for name, info in states.items():

        if only and name != only:
            continue

        on = bool(info.get("enabled"))
        loaded = name in analyzers

        last = info.get("last_candle", {}) or {}

        fresh = sum(
            1
            for symbol in symbols
            if last.get(symbol) == expected
        )

        print(
            f"\n{name}: "
            f"{'ON' if on else 'OFF'}"
            f" | loaded: {'YES' if loaded else 'NO'}"
            f" | signals: {info.get('signals', 0)}"
            f" | fresh coins: {fresh}/{len(symbols)}"
        )

        if info.get("last_error"):
            print(
                "Last error:",
                info["last_error"]
            )

        if on and loaded:
            enabled.append(name)

    if active_trades is not None:

        try:

            trades = active_trades()

            print(
                f"\nCurrently active/pending trades: {len(trades)}"
            )

        except Exception as exc:
            print(
                "Could not read active trades:",
                exc
            )

    return enabled


# ============================================================
# TRADE SIMULATION
# ============================================================

def entry_touched(
    candle: dict,
    entry: float
) -> bool:

    return (
        candle["low"] <= entry <= candle["high"]
    )


def get_exit(
    trade: dict,
    candle: dict
):

    direction = trade["direction"]

    tp = trade["tp"]
    sl = trade["sl"]

    if direction == "LONG":

        stop_hit = candle["low"] <= sl
        target_hit = candle["high"] >= tp

    else:

        stop_hit = candle["high"] >= sl
        target_hit = candle["low"] <= tp

    # Conservative rule:
    # If TP and SL are touched in the same candle,
    # count the trade as SL.
    if stop_hit:
        return sl, "SL"

    if target_hit:
        return tp, "TP"

    return None, None


def calculate_trade_pnl(
    trade: dict,
    exit_price: float
) -> tuple[float, float, float]:

    entry = trade["entry"]
    quantity = trade["quantity"]

    if trade["direction"] == "LONG":
        gross = (exit_price - entry) * quantity
    else:
        gross = (entry - exit_price) * quantity

    entry_fee = entry * quantity * FEE_RATE
    exit_fee = exit_price * quantity * FEE_RATE

    fees = entry_fee + exit_fee

    net = gross - fees

    return gross, fees, net


# ============================================================
# BACKTEST ONE SYMBOL
# ============================================================

def backtest_symbol(
    analyzer,
    candles: list[dict],
    symbol: str = "UNKNOWN",
) -> dict:

    result = {
        "signals": 0,
        "wins": 0,
        "losses": 0,
        "tp": 0,
        "sl": 0,
        "pnl": 0.0,
        "fees": 0.0,
        "gross": 0.0,
        "closed": 0,
        "pending": 0,
        "open": 0,
        "balance": CAPITAL,
        "balance_history": [],
    }

    if len(candles) < WINDOW:
        return result

    balance = CAPITAL

    active_orders = []

    errors = Counter()

    first_test_time = candles[-1]["timestamp"] - (
        DAYS * 24 * HOUR_MS
    )

    # Jump directly to the first candle in the test period.
    # The preceding WINDOW candles remain available as warm-up data.
    timestamps = [item["timestamp"] for item in candles]
    first_test_index = max(
        WINDOW - 1,
        bisect_left(timestamps, first_test_time)
    )

    total_steps = max(
        1,
        len(candles) - first_test_index
    )

    last_progress = 0

    for candle_index in range(first_test_index, len(candles)):

        candle = candles[candle_index]
        candle_time = candle["timestamp"]

        step = candle_index - first_test_index + 1

        if (
            step - last_progress >= PROGRESS_EVERY
            or step >= total_steps
        ):

            show_progress(
                "Backtesting:",
                step,
                total_steps
            )

            last_progress = step

        closed_this_candle = False

        # ----------------------------------------------------
        # 1. PROCESS EXISTING ORDERS
        # ----------------------------------------------------

        for trade in active_orders:

            if trade["status"] == "CLOSED":
                continue

            # Pending LIMIT order.
            if trade["status"] == "PENDING":

                if not entry_touched(
                    candle,
                    trade["entry"]
                ):
                    continue

                trade["status"] = "OPEN"
                trade["entry_time"] = candle_time

            # Check TP / SL.
            exit_price, reason = get_exit(
                trade,
                candle
            )

            if reason is None:
                continue

            gross, fees, net = calculate_trade_pnl(
                trade,
                exit_price
            )

            trade["status"] = "CLOSED"

            result["closed"] += 1
            result["gross"] += gross
            result["fees"] += fees
            result["pnl"] += net

            balance += net

            result["balance_history"].append({
                "timestamp": candle_time,
                "balance": balance,
                "pnl": net,
                "reason": reason,
            })

            # Print the balance after every closed trade.
            print()
            print(
                f"  [{ts(candle_time)}] {symbol:<12} "
                f"{reason:<2} | Trade PnL: {net:+.4f} USDT "
                f"| BALANCE: {balance:.4f} USDT"
            )

            if net > 0:
                result["wins"] += 1
            else:
                result["losses"] += 1

            if reason == "TP":
                result["tp"] += 1
            else:
                result["sl"] += 1

            closed_this_candle = True

        # Remove completed orders.
        active_orders = [
            trade
            for trade in active_orders
            if trade["status"] != "CLOSED"
        ]

        # ----------------------------------------------------
        # 2. WAIT UNTIL NEXT CANDLE AFTER CLOSING
        # ----------------------------------------------------

        if active_orders or closed_this_candle:
            continue

        if balance <= 0:
            break

        # ----------------------------------------------------
        # 3. ANALYZE CURRENT CANDLE
        # ----------------------------------------------------

        window = candles[
            candle_index - WINDOW + 1:candle_index + 1
        ]

        try:

            signal = analyzer(
                window,
                current_price=float(candle["close"]),
                capital=balance,
                open_positions=0,
                pending_orders=0,
            )

        except Exception as exc:

            message = (
                f"{type(exc).__name__}: {exc}"
            )

            errors[message] += 1

            continue

        if not signal or not signal.get("orders"):
            continue

        result["signals"] += 1

        direction = str(
            signal.get("direction", "")
        ).upper()

        if direction not in ("LONG", "SHORT"):
            continue

        # ----------------------------------------------------
        # 4. CREATE PENDING ORDERS
        # ----------------------------------------------------

        for order in signal["orders"]:

            entry = number(order.get("entry"))
            tp = number(order.get("tp"))
            sl = number(order.get("sl"))

            order_direction = str(
                order.get("direction", direction)
            ).upper()

            if (
                entry <= 0
                or tp <= 0
                or sl <= 0
                or order_direction not in ("LONG", "SHORT")
            ):
                continue

            quantity = number(
                order.get("quantity")
            )

            position_value = number(
                order.get("position_value")
            )

            # Use the strategy's quantity if available.
            if quantity <= 0:

                if position_value > 0:
                    quantity = position_value / entry

                else:

                    position_value = (
                        balance *
                        POSITION_CAPITAL_PERCENT / 100.0
                    )

                    quantity = position_value / entry

            if quantity <= 0:
                continue

            active_orders.append({
                "direction": order_direction,
                "entry": entry,
                "tp": tp,
                "sl": sl,
                "quantity": quantity,
                "position_value": quantity * entry,
                "status": "PENDING",
                "signal_time": candle_time,
            })

    # --------------------------------------------------------
    # REMAINING ORDERS
    # --------------------------------------------------------

    result["pending"] = sum(
        1
        for trade in active_orders
        if trade["status"] == "PENDING"
    )

    result["open"] = sum(
        1
        for trade in active_orders
        if trade["status"] == "OPEN"
    )

    result["balance"] = balance
    result["errors"] = errors

    return result


# ============================================================
# BACKTEST STRATEGY
# ============================================================

def replay(
    name: str,
    analyzer,
    symbols: list[str],
    days: int,
    candles_cache: dict[str, list[dict]],
) -> dict:

    section(
        f"RESULTS: {name} | LAST {days} DAYS"
    )

    started = time.time()

    total = {
        "wins": 0,
        "losses": 0,
        "tp": 0,
        "sl": 0,
        "closed": 0,
        "pnl": 0.0,
        "fees": 0.0,
        "gross": 0.0,
        "signals": 0,
        "pending": 0,
        "open": 0,
        "coins": 0,
        "initial": 0.0,
        "positive_coins": 0,
        "negative_coins": 0,
        "zero_coins": 0,
    }

    positive = []
    negative = []
    neutral = []

    for index, symbol in enumerate(symbols, start=1):

        print(
            f"\n\n[{index}/{len(symbols)}] Loading {symbol}..."
        )

        candles = candles_cache.get(symbol)

        if not candles:
            print("  No cached candles for this symbol; skipping.")
            continue

        if len(candles) < WINDOW:

            print(
                f"  Not enough historical candles: {len(candles)}"
            )

            continue

        print(
            f"\n  Starting backtest for {symbol}..."
        )

        try:

            stats = backtest_symbol(
                analyzer,
                candles,
                symbol
            )

        except Exception:

            print("\n  BACKTEST ERROR:")
            traceback.print_exc()
            continue

        total["coins"] += 1
        total["initial"] += CAPITAL

        for key in (
            "wins",
            "losses",
            "tp",
            "sl",
            "closed",
            "signals",
            "pending",
            "open",
        ):
            total[key] += stats[key]

        total["pnl"] += stats["pnl"]
        total["fees"] += stats["fees"]
        total["gross"] += stats["gross"]

        item = {
            "symbol": symbol,
            **stats,
        }

        if stats["pnl"] > 0:
            positive.append(item)
            total["positive_coins"] += 1

        elif stats["pnl"] < 0:
            negative.append(item)
            total["negative_coins"] += 1

        else:
            neutral.append(item)
            total["zero_coins"] += 1

        pnl_sign = "+" if stats["pnl"] > 0 else ""

        print(
            f"\n  {symbol:<14}"
            f" TP: {stats['tp']:<5}"
            f" SL: {stats['sl']:<5}"
            f" WIN: {stats['wins']:<5}"
            f" LOSS: {stats['losses']:<5}"
            f" PnL: {pnl_sign}{stats['pnl']:.4f} USDT"
        )

        print(
            f"  FINAL BALANCE {symbol}: "
            f"${stats['balance']:.4f} "
            f"({stats['pnl']:+.4f} USDT)"
        )

        if stats["pending"] or stats["open"]:

            print(
                f"  Still active: "
                f"{stats['pending']} pending, "
                f"{stats['open']} open"
            )

        if stats["errors"]:

            print(
                f"  Analyzer errors: "
                f"{sum(stats['errors'].values())}"
            )

    # --------------------------------------------------------
    # FINAL REPORT
    # --------------------------------------------------------

    section(f"FINAL REPORT: {name}")

    win_rate = (
        total["wins"] / total["closed"] * 100
        if total["closed"] else 0.0
    )

    roi = (
        total["pnl"] / total["initial"] * 100
        if total["initial"] else 0.0
    )

    print(f"Period: {days} days")
    print(f"Coins tested: {total['coins']}")

    print(
        f"Profitable coins: {total['positive_coins']}"
    )

    print(
        f"Losing coins: {total['negative_coins']}"
    )

    print(
        f"Zero-result coins: {total['zero_coins']}"
    )

    print(
        f"Initial capital: ${total['initial']:.2f}"
    )

    print(
        f"FINAL COMBINED BALANCE: "
        f"${total['initial'] + total['pnl']:.4f}"
    )

    print(
        f"Closed trades: {total['closed']}"
    )

    print(
        f"Profitable trades: {total['wins']}"
    )

    print(
        f"Losing trades: {total['losses']}"
    )

    print(
        f"Take Profit: {total['tp']}"
    )

    print(
        f"Stop Loss: {total['sl']}"
    )

    print(
        f"Win Rate: {win_rate:.2f}%"
    )

    print(
        f"Gross PnL: {total['gross']:+.4f} USDT"
    )

    print(
        f"Fees: {total['fees']:.4f} USDT"
    )

    print(
        f"NET PnL: {total['pnl']:+.4f} USDT"
    )

    print(
        f"ROI: {roi:+.2f}%"
    )

    print(
        f"Pending orders: {total['pending']}"
    )

    print(
        f"Open trades: {total['open']}"
    )

    # --------------------------------------------------------
    # PROFITABLE COINS
    # --------------------------------------------------------

    section("GREEN: PROFITABLE COINS")

    if positive:

        positive.sort(
            key=lambda item: item["pnl"],
            reverse=True
        )

        for item in positive:

            print(
                f"+ {item['symbol']:<14}"
                f" PnL: +{item['pnl']:.4f} USDT"
                f" | TP: {item['tp']}"
                f" | SL: {item['sl']}"
                f" | Win: {item['wins']}"
                f" | Loss: {item['losses']}"
            )

    else:
        print("No profitable coins.")

    # --------------------------------------------------------
    # LOSING COINS
    # --------------------------------------------------------

    section("RED: LOSING COINS")

    if negative:

        negative.sort(
            key=lambda item: item["pnl"]
        )

        for item in negative:

            print(
                f"- {item['symbol']:<14}"
                f" PnL: {item['pnl']:.4f} USDT"
                f" | TP: {item['tp']}"
                f" | SL: {item['sl']}"
                f" | Win: {item['wins']}"
                f" | Loss: {item['losses']}"
            )

    else:
        print("No losing coins.")

    # --------------------------------------------------------
    # ZERO RESULT
    # --------------------------------------------------------

    section("ZERO RESULT COINS")

    if neutral:

        for item in neutral:

            print(
                f"= {item['symbol']:<14}"
                f" PnL: {item['pnl']:.4f} USDT"
                f" | Closed trades: {item['closed']}"
            )

    else:
        print("No zero-result coins.")

    print(
        f"\nBacktest time: {time.time() - started:.1f} seconds"
    )

    return total


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--only",
        help="Check only one strategy"
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        help="Coins to test"
    )

    args = parser.parse_args()

    days = DAYS

    if days <= 0:
        raise ValueError(
            "DAYS must be greater than zero."
        )

    symbols = (
        [symbol.upper() for symbol in args.symbols]
        if args.symbols
        else get_symbols()
    )

    if not symbols:
        print("ERROR: Empty symbol list.")
        return

    print("\n" + "=" * 75)
    print("                 MASB HISTORICAL BACKTEST")
    print("=" * 75)

    print(f"Period: {days} days")
    print(f"Capital per coin: ${CAPITAL:.2f}")
    print(f"Coins: {len(symbols)}")
    print(f"Analysis window: {WINDOW} candles")
    print(f"Backtest progress refresh: every {PROGRESS_EVERY:,} candles")
    print("Candles are cached on disk; first run downloads history, later runs update only new candles.")
    print(f"Disk cache folder: {CACHE_DIR}")
    print(
        f"Commission per side: {FEE_RATE * 100:.3f}%"
    )

    enabled = check_pipeline(
        args.only,
        symbols
    )

    analyzers, _, _ = discover_strategies()

    if not enabled:

        print(
            "\nNo enabled and loaded strategies."
        )

        return

    # Download each symbol once. The same historical candles are reused
    # for every enabled strategy, avoiding repeated 3-year API downloads.
    section("2. DOWNLOADING HISTORICAL CANDLES")

    candles_cache: dict[str, list[dict]] = {}
    download_started = time.time()

    for index, symbol in enumerate(symbols, start=1):
        print(f"\n[{index}/{len(symbols)}] {symbol}")

        try:
            candles = get_historical_klines(symbol, days)
            if len(candles) >= WINDOW:
                candles_cache[symbol] = candles
            else:
                print(f"  Skipped: only {len(candles)} candles available.")
        except Exception as exc:
            print(f"\n  DATA ERROR: {exc}")

    print(
        f"\nHistorical download finished in "
        f"{time.time() - download_started:.1f} seconds. "
        f"Cached symbols: {len(candles_cache)}/{len(symbols)}"
    )

    if not candles_cache:
        print("No historical data could be downloaded.")
        return

    for index, name in enumerate(enabled, start=1):

        section(
            f"STRATEGY {index}/{len(enabled)}: {name}"
        )

        replay(
            name,
            analyzers[name],
            symbols,
            days,
            candles_cache,
        )

    section("BACKTEST FINISHED")

    print(
        "Only closed trades are counted as wins or losses."
    )

    print(
        "Open and unfilled orders are shown separately."
    )

    print(
        "Each coin is tested with an independent $100 balance; combined balance is the sum of all coin accounts."
    )


if __name__ == "__main__":
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"backtest.txt"

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_file = log_path.open("w", encoding="utf-8")

    sys.stdout = Tee(original_stdout, log_file)
    sys.stderr = sys.stdout

    try:
        main()
    except BaseException:
        traceback.print_exc()
    finally:
        sys.stdout.flush()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()
        print(f"Лог сохранён: {log_path}")