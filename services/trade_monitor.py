from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
# Используем объектный API matplotlib (Figure + FigureCanvasAgg) вместо pyplot:
# pyplot хранит глобальное состояние и НЕ потокобезопасен, а графики строятся
# из asyncio.to_thread() сразу несколькими задачами (сканер + монитор).
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from config import DATA_DIR

logger = logging.getLogger("MASB.TradeMonitor")
STATE_FILE = DATA_DIR / "trade_monitor.json"
_LOCK = threading.RLock()
_RENDER_LOCK = threading.Lock()  # один график за раз
MAX_HISTORY = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        return {"schema_version": 1, "trades": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("trades", []), list):
            raise ValueError("Неверная структура trade_monitor.json")
        data.setdefault("schema_version", 1)
        data.setdefault("trades", [])
        return data
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Не удалось прочитать {STATE_FILE}: {exc}") from exc


def _write(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def all_trades() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(item) for item in _read()["trades"]]


def active_trades(strategy: str | None = None, symbol: str | None = None) -> list[dict[str, Any]]:
    items = [t for t in all_trades() if t.get("status") in {"PENDING", "OPEN"}]
    if strategy is not None:
        items = [t for t in items if t.get("strategy") == strategy]
    if symbol is not None:
        items = [t for t in items if t.get("symbol") == symbol]
    return items


def add_signal_orders(
    strategy: str,
    symbol: str,
    signal: dict[str, Any],
    candle_timestamp: int,
) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    with _LOCK:
        data = _read()
        trades = data["trades"]
        existing_ids = {t.get("id") for t in trades}
        for order in signal.get("orders", []):
            identity = "|".join([
                strategy, symbol, signal.get("direction", ""),
                str(signal.get("swing_start_timestamp") or ""),
                str(signal.get("swing_end_timestamp") or ""),
                f"{float(signal.get('swing_start', 0)):.12g}",
                f"{float(signal.get('swing_end', 0)):.12g}",
                f"{float(order.get('entry', 0)):.12g}",
                f"{float(order.get('tp', 0)):.12g}",
                f"{float(order.get('sl', 0)):.12g}",
                str(order.get("entry_number", 0)),
            ])
            trade_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
            if trade_id in existing_ids:
                continue
            trade = {
                "id": trade_id,
                "strategy": strategy,
                "symbol": symbol,
                "direction": signal["direction"],
                "status": "PENDING",
                "created_at": _now(),
                "signal_candle_timestamp": int(candle_timestamp),
                "entry_number": int(order.get("entry_number", 1)),
                "entry": float(order["entry"]),
                "tp": float(order["tp"]),
                "sl": float(order["sl"]),
                "quantity": float(order.get("quantity", 0.0)),
                "position_value": float(order.get("position_value", 0.0)),
                "entry_fib": float(order.get("entry_fib", 0.0)),
                "tp_fib": float(order.get("tp_fib", 0.0)),
                "is_green_tp": bool(order.get("is_green_tp", False)),
                "rr": float(order.get("rr", signal.get("rr", 3.0))),
                "swing_start": float(signal["swing_start"]),
                "swing_end": float(signal["swing_end"]),
                "swing_start_index": int(signal.get("swing_start_index", 0)),
                "swing_end_index": int(signal.get("swing_end_index", 0)),
                "swing_start_timestamp": signal.get("swing_start_timestamp"),
                "swing_end_timestamp": signal.get("swing_end_timestamp"),
                "fibonacci": signal.get("fibonacci", {}),
                "green_fibonacci": signal.get("green_fibonacci", {}),
                "current_price_at_signal": float(signal.get("current_price", 0.0)),
                "entry_time": None,
                "exit_time": None,
                "exit_price": None,
                "pnl_gross": None,
                "fees": None,
                "pnl_net": None,
                "close_reason": None,
                "last_checked_candle": None,
            }
            trades.append(trade)
            existing_ids.add(trade_id)
            created.append(dict(trade))
        data["trades"] = trades[-MAX_HISTORY:]
        _write(data)
    return created


def update_trade(trade_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    with _LOCK:
        data = _read()
        for trade in data["trades"]:
            if trade.get("id") == trade_id:
                trade.update(updates)
                _write(data)
                return dict(trade)
    return None


def trade_stats(strategy: str | None = None) -> dict[str, Any]:
    trades = all_trades()
    if strategy:
        trades = [t for t in trades if t.get("strategy") == strategy]
    closed = [t for t in trades if t.get("status") in {"TP", "SL"}]
    wins = sum(1 for t in closed if t.get("status") == "TP")
    losses = sum(1 for t in closed if t.get("status") == "SL")
    pnl = sum(float(t.get("pnl_net") or 0.0) for t in closed)
    return {
        "pending": sum(1 for t in trades if t.get("status") == "PENDING"),
        "open": sum(1 for t in trades if t.get("status") == "OPEN"),
        "closed": len(closed),
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / len(closed) * 100.0) if closed else 0.0,
        "pnl_net": pnl,
        "balance": 100.0 + pnl,
        "trades": trades,
    }


def _candle_datetime(candle: dict[str, Any]) -> datetime:
    raw = candle.get("timestamp", candle.get("time", 0))
    try:
        stamp = float(raw)
        if stamp > 10_000_000_000:
            stamp /= 1000.0
        return datetime.fromtimestamp(stamp, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


def _finite(value: Any, default: float | None = None) -> float | None:
    """float(value), если это конечное число, иначе default (None/NaN/inf/мусор)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def render_trade_chart(
    candles: list[dict[str, Any]],
    trade: dict[str, Any],
    phase: str = "SIGNAL",
) -> BytesIO:
    # matplotlib нельзя безопасно использовать из нескольких потоков одновременно.
    with _RENDER_LOCK:
        return _render_trade_chart_locked(candles, trade, phase)


def _render_trade_chart_locked(
    candles: list[dict[str, Any]],
    trade: dict[str, Any],
    phase: str = "SIGNAL",
) -> BytesIO:

    if not candles:
        raise ValueError("Для графика нет свечей")

    # Выбрасываем свечи с нечисловыми/нулевыми ценами, чтобы они не ломали оси.
    candles = [
        c for c in candles
        if all(_finite(c.get(k)) is not None for k in ("open", "high", "low", "close"))
    ]
    if not candles:
        raise ValueError("Для графика нет корректных свечей")

    visible = candles[-160:]
    dates = [_candle_datetime(c) for c in visible]
    n = len(visible)
    x_values = list(range(n))
    right = n + max(12, n // 7)

    bg = "#131722"
    fg = "#d1d4dc"
    grid = "#2a2e39"
    up = "#26a69a"
    down = "#ef5350"
    entry_color = "#ff9800"
    tp_color = "#26a69a"
    sl_color = "#ef5350"
    fib_color = "#b2b5be"
    green_fib_color = "#00e676"

    fig = Figure(figsize=(12, 7), dpi=120)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    highs = [float(c["high"]) for c in visible]
    lows = [float(c["low"]) for c in visible]
    candle_span = (max(highs) - min(lows)) or max(abs(float(visible[-1]["close"])), 1.0) * 0.001
    candle_width = 0.68
    for i, candle in enumerate(visible):
        opn = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        color = up if close >= opn else down
        ax.vlines(i, low, high, color=color, linewidth=0.85, zorder=2)
        body = abs(close - opn) or candle_span * 0.0005
        ax.bar(i, body, bottom=min(opn, close), width=candle_width,
               color=color, edgecolor=color, linewidth=0.45, zorder=3)

    def nearest_x(raw_timestamp: Any, fallback_index: int | None = None) -> int | None:
        if raw_timestamp is not None:
            try:
                if isinstance(raw_timestamp, str) and not raw_timestamp.strip().replace(".", "", 1).isdigit():
                    target = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
                    if target.tzinfo is None:
                        target = target.replace(tzinfo=timezone.utc)
                    else:
                        target = target.astimezone(timezone.utc)
                else:
                    stamp = float(raw_timestamp)
                    if stamp > 10_000_000_000:
                        stamp /= 1000.0
                    target = datetime.fromtimestamp(stamp, tz=timezone.utc)
                idx = min(range(n), key=lambda j: abs((dates[j] - target).total_seconds()))
                # One-hour tolerance matches the bot's 1h candles but also
                # accepts an exact timestamp in the current candle.
                if abs((dates[idx] - target).total_seconds()) <= 3600:
                    return idx
            except (TypeError, ValueError, OSError, OverflowError):
                pass
        if fallback_index is not None:
            offset = max(0, len(candles) - n)
            local = int(fallback_index) - offset
            if 0 <= local < n:
                return local
        return None

    start_x = nearest_x(trade.get("swing_start_timestamp"), trade.get("swing_start_index"))
    end_x = nearest_x(trade.get("swing_end_timestamp"), trade.get("swing_end_index"))
    swing_start = _finite(trade.get("swing_start"), 0.0) or 0.0
    swing_end = _finite(trade.get("swing_end"), 0.0) or 0.0
    if start_x is not None and end_x is not None and swing_start > 0 and swing_end > 0:
        ax.plot([start_x, end_x], [swing_start, swing_end], color=entry_color,
                linewidth=2.1, zorder=5, label="ZigZag")
        ax.scatter([start_x, end_x], [swing_start, swing_end], marker="o", s=58,
                   color=entry_color, edgecolors="#fff3e0", linewidths=0.8, zorder=6)
        ax.annotate("ZZ start", (start_x, swing_start), xytext=(4, 7),
                    textcoords="offset points", color=fg, fontsize=7)
        ax.annotate("ZZ confirm", (end_x, swing_end), xytext=(4, 7),
                    textcoords="offset points", color=fg, fontsize=7)

    levels: dict[float, dict[str, Any]] = {}

    def add_level(price: Any, label: str, color: str, style: str, width: float, priority: int) -> None:
        value = _finite(price)
        if value is None or value <= 0:
            return
        key = round(value, 10)
        current = levels.setdefault(key, {"price": value, "labels": [], "color": color,
                                          "style": style, "width": width, "priority": -1})
        if label not in current["labels"]:
            current["labels"].append(label)
        if priority >= current["priority"]:
            current.update(color=color, style=style, width=width, priority=priority)

    fib_levels = trade.get("fibonacci")
    green_levels = trade.get("green_fibonacci")
    for level, price in (fib_levels.items() if isinstance(fib_levels, dict) else ()):
        add_level(price, f"Fib {level}", fib_color, "-", 0.75, 0)
    for level, price in (green_levels.items() if isinstance(green_levels, dict) else ()):
        add_level(price, f"Green Fib {level}", green_fib_color, "--", 0.9, 1)

    entry = _finite(trade.get("entry"))
    tp = _finite(trade.get("tp"))
    sl = _finite(trade.get("sl"))
    if entry is None or tp is None or sl is None:
        raise ValueError(f"Некорректные Entry/TP/SL в сделке: {trade.get('entry')}/{trade.get('tp')}/{trade.get('sl')}")
    add_level(entry, f"ENTRY {trade.get('entry_number', 1)}", entry_color, "-", 2.2, 4)
    add_level(tp, "TP", tp_color, ":", 1.9, 3)
    add_level(sl, "SL", sl_color, ":", 1.9, 3)

    closed = phase in {"TP", "SL"} or trade.get("status") in {"TP", "SL"}
    exit_price = _finite(trade.get("exit_price"))
    if closed and exit_price is not None:
        add_level(exit_price, "EXIT", tp_color if phase == "TP" else sl_color, "--", 1.8, 5)

    for level in levels.values():
        ax.plot([max(0, end_x if end_x is not None else n - 1), right - 1],
                [level["price"], level["price"]], color=level["color"],
                linestyle=level["style"], linewidth=level["width"], zorder=4)
        ax.text(right - 0.5, level["price"],
                f" {' · '.join(level['labels'])}  {level['price']:.8g}",
                color=level["color"], va="center", ha="left", fontsize=8.0)

    # Show the fill and the final exit on the relevant candle. This makes the
    # TP and especially the SL result chart distinct from the initial signal.
    entry_x = nearest_x(trade.get("entry_time"))
    if entry_x is None:
        entry_x = max(0, min(n - 1, end_x if end_x is not None else n - 1))
    if phase in {"ENTRY", "TP", "SL"}:
        ax.scatter([entry_x], [entry], marker="^" if trade.get("direction") == "LONG" else "v",
                   s=145, color=entry_color, edgecolors="#fff3e0", linewidths=0.9,
                   zorder=7, label="ENTRY filled")

    if closed and exit_price is not None:
        exit_x = n - 1
        exit_color = tp_color if phase == "TP" else sl_color
        ax.plot([entry_x, exit_x], [entry, exit_price], color=entry_color,
                linewidth=1.6, alpha=0.9, zorder=5)
        ax.scatter([exit_x], [exit_price], marker="*", s=250, color=exit_color,
                   edgecolors="#ffffff", linewidths=0.7, zorder=8,
                   label="TP EXIT" if phase == "TP" else "SL EXIT")
        ax.annotate("TP HIT" if phase == "TP" else "SL HIT", (exit_x, exit_price),
                    xytext=(6, 10 if phase == "TP" else -16), textcoords="offset points",
                    color=exit_color, fontsize=10, fontweight="bold")

    phase_titles = {
        "SIGNAL": "НОВЫЙ СИГНАЛ · ОЖИДАНИЕ ENTRY",
        "ENTRY": "ENTRY ДОСТИГНУТ · ВИРТУАЛЬНЫЙ ВХОД",
        "TP": "TAKE PROFIT ДОСТИГНУТ",
        "SL": "STOP LOSS ДОСТИГНУТ",
    }
    side = str(trade.get("direction", ""))
    result_suffix = ""
    if closed:
        try:
            result_suffix = f" · PnL {float(trade.get('pnl_net') or 0.0):+.4f} USDT"
        except (TypeError, ValueError):
            result_suffix = ""
    ax.set_title(
        f"{trade.get('symbol', '')} · 1h · {side} · {trade.get('strategy', '')} · "
        f"{phase_titles.get(phase, phase)}{result_suffix}",
        color=fg, fontsize=11, pad=14, fontweight="bold",
    )
    ax.set_ylabel("Цена (USDT)", color=fg)
    tick_step = max(1, n // 8)
    ticks = list(range(0, n, tick_step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([dates[i].strftime("%d.%m %H:%M") for i in ticks], color=fg, fontsize=8)
    ax.tick_params(axis="y", colors=fg, labelsize=8)
    ax.grid(color=grid, linewidth=0.5, alpha=0.9)
    for spine in ax.spines.values():
        spine.set_color(grid)

    all_prices = lows + highs + [v["price"] for v in levels.values()]
    if swing_start > 0:
        all_prices.append(swing_start)
    if swing_end > 0:
        all_prices.append(swing_end)
    if exit_price is not None and closed:
        all_prices.append(float(exit_price))
    low_y, high_y = min(all_prices), max(all_prices)
    pad = (high_y - low_y) * 0.07 or max(abs(high_y), 1.0) * 0.002
    ax.set_ylim(low_y - pad, high_y + pad)
    ax.set_xlim(-1, right + max(12, n // 5))
    handles, _labels = ax.get_legend_handles_labels()
    if handles:
        legend = ax.legend(loc="upper left", fontsize=8, facecolor="#1f2937", edgecolor=grid)
        for legend_text in legend.get_texts():
            legend_text.set_color("white")
    fig.tight_layout()

    output = BytesIO()
    fig.savefig(output, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    output.seek(0)
    output.name = f"{trade.get('symbol', 'MASB')}_{phase.lower()}.png"
    return output

def entry_hit(trade: dict[str, Any], candle: dict[str, Any]) -> bool:
    low, high = float(candle["low"]), float(candle["high"])
    entry = float(trade["entry"])
    return low <= entry if trade["direction"] == "LONG" else high >= entry


def touched_exit(trade: dict[str, Any], candle: dict[str, Any]) -> str | None:
    low, high = float(candle["low"]), float(candle["high"])
    if trade["direction"] == "LONG":
        sl_hit = low <= float(trade["sl"])
        tp_hit = high >= float(trade["tp"])
    else:
        sl_hit = high >= float(trade["sl"])
        tp_hit = low <= float(trade["tp"])
    if sl_hit:
        return "SL"
    if tp_hit:
        return "TP"
    return None


def close_trade(trade: dict[str, Any], reason: str, exit_price: float) -> dict[str, Any]:
    entry = float(trade["entry"])
    qty = float(trade.get("quantity", 0.0))
    direction_sign = 1.0 if trade["direction"] == "LONG" else -1.0
    gross = (float(exit_price) - entry) * qty * direction_sign
    fees = (entry * qty + float(exit_price) * qty) * 0.00055
    return {
        "status": reason,
        "exit_time": _now(),
        "exit_price": float(exit_price),
        "pnl_gross": gross,
        "fees": fees,
        "pnl_net": gross - fees,
        "close_reason": reason,
    }