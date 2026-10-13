from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from config import DATA_DIR

logger = logging.getLogger("MASB.TradeMonitor")
STATE_FILE = DATA_DIR / "trade_monitor.json"
_LOCK = threading.RLock()
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
    """Persist pending virtual trades; the same setup is never duplicated."""
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


def render_trade_chart(
    candles: list[dict[str, Any]],
    trade: dict[str, Any],
    phase: str = "SIGNAL",
) -> BytesIO:
    """Draw a self-contained candlestick chart with Entry/TP/SL and Fib/ZigZag."""
    if not candles:
        raise ValueError("Для графика нет свечей")
    visible = candles[-100:]
    dates = [_candle_datetime(c) for c in visible]
    x_values = mdates.date2num(dates)
    fig, ax = plt.subplots(figsize=(12, 7), dpi=130)
    fig.patch.set_facecolor("#111827")
    ax.set_facecolor("#111827")
    width = (x_values[-1] - x_values[0]) / max(30, len(x_values)) * 0.68 if len(x_values) > 1 else 0.02

    for x, candle in zip(x_values, visible):
        opn = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        bullish = close >= opn
        candle_color = "#22c55e" if bullish else "#ef4444"
        ax.vlines(x, low, high, color=candle_color, linewidth=1.0, zorder=2)
        bottom = min(opn, close)
        height = max(abs(close - opn), max(abs(close), 1.0) * 0.00002)
        ax.add_patch(Rectangle((x - width / 2, bottom), width, height,
                               facecolor=candle_color, edgecolor=candle_color,
                               linewidth=0.5, zorder=3))

    entry = float(trade["entry"])
    tp = float(trade["tp"])
    sl = float(trade["sl"])
    ax.axhline(entry, color="#f59e0b", linewidth=2.0, linestyle="-", label=f"ENTRY {entry:.8g}")
    ax.axhline(tp, color="#22c55e", linewidth=2.0, linestyle="--", label=f"TP {tp:.8g}")
    ax.axhline(sl, color="#ef4444", linewidth=2.0, linestyle="--", label=f"SL {sl:.8g}")

    # Show Fibonacci retracement/extension levels used by fibZZ10.
    fibs = trade.get("fibonacci", {})
    for level, price in fibs.items():
        try:
            level_price = float(price)
        except (TypeError, ValueError):
            continue
        ax.axhline(level_price, color="#94a3b8", linewidth=0.7, alpha=0.35)
        ax.text(x_values[-1], level_price, f" Fib {level}", color="#cbd5e1", fontsize=7,
                va="bottom", ha="right", alpha=0.8)

    # Mark ZigZag swing pivots using their timestamps, so later charts still align.
    start_i = int(trade.get("swing_start_index", -1))
    end_i = int(trade.get("swing_end_index", -1))
    offset = max(0, len(candles) - len(visible))
    pivot_specs = [
        (trade.get("swing_start_timestamp"), start_i, float(trade.get("swing_start", 0)), "o", "ZigZag start"),
        (trade.get("swing_end_timestamp"), end_i, float(trade.get("swing_end", 0)), "D", "ZigZag confirm"),
    ]
    for pivot_timestamp, idx, price, marker, label in pivot_specs:
        local = None
        if pivot_timestamp is not None:
            try:
                pivot_ms = int(pivot_timestamp)
                if pivot_ms > 10_000_000_000:
                    pivot_ms //= 1000
                pivot_dt = datetime.fromtimestamp(pivot_ms, tz=timezone.utc)
                nearest = min(range(len(dates)), key=lambda i: abs((dates[i] - pivot_dt).total_seconds()))
                if abs((dates[nearest] - pivot_dt).total_seconds()) <= 3600:
                    local = nearest
            except (TypeError, ValueError, OSError):
                local = None
        if local is None:
            local = idx - offset
        if 0 <= local < len(x_values) and price > 0:
            ax.scatter([x_values[local]], [price], marker=marker, s=70,
                       color="#38bdf8", edgecolors="#e0f2fe", linewidths=0.8,
                       zorder=5, label=label)

    green_fibs = trade.get("green_fibonacci", {})
    for level, price in green_fibs.items():
        try:
            level_price = float(price)
        except (TypeError, ValueError):
            continue
        ax.axhline(level_price, color="#4ade80", linewidth=0.7, alpha=0.3, linestyle=":")
        ax.text(x_values[-1], level_price, f" Green Fib {level}", color="#86efac", fontsize=7,
                va="bottom", ha="right", alpha=0.8)

    phase_titles = {
        "SIGNAL": "SIGNAL / ОЖИДАНИЕ ENTRY",
        "ENTRY": "ENTRY ДОСТИГНУТ — ВИРТУАЛЬНЫЙ ВХОД",
        "TP": "TAKE PROFIT ДОСТИГНУТ",
        "SL": "STOP LOSS ДОСТИГНУТ",
    }
    ax.set_title(f"MASB | {trade.get('symbol', '')} | {trade.get('strategy', '')} | {phase_titles.get(phase, phase)}",
                 color="white", fontsize=12, pad=14, weight="bold")
    ax.set_ylabel("Цена (USDT)", color="#e5e7eb")
    ax.tick_params(axis="x", colors="#cbd5e1", labelsize=8)
    ax.tick_params(axis="y", colors="#cbd5e1", labelsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M", tz=timezone.utc))
    ax.grid(True, color="#334155", alpha=0.5, linewidth=0.5)
    for spine in ax.spines.values():
        spine.set_color("#475569")
    legend = ax.legend(loc="upper left", fontsize=8, facecolor="#1f2937", edgecolor="#475569")
    for text_item in legend.get_texts():
        text_item.set_color("white")
    ax.margins(x=0.03, y=0.12)
    fig.tight_layout()
    output = BytesIO()
    fig.savefig(output, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    output.seek(0)
    output.name = f"{trade.get('symbol', 'MASB')}_{phase.lower()}.png"
    return output


def entry_hit(trade: dict[str, Any], candle: dict[str, Any]) -> bool:
    low, high = float(candle["low"]), float(candle["high"])
    entry = float(trade["entry"])
    return low <= entry if trade["direction"] == "LONG" else high >= entry


def touched_exit(trade: dict[str, Any], candle: dict[str, Any]) -> str | None:
    """If TP and SL touch the same candle, always return SL (conservative rule)."""
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
