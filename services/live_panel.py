from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from config import DATA_DIR

logger = logging.getLogger("MASB.LivePanel")

STATE_FILE = DATA_DIR / "live_panel.json"
_LOCK = threading.RLock()

CANDLES = 72  # 72 часовые свечи = 3 суток на графике каждой монеты


# ============================================================
# STATE: enabled + message_id для каждой монеты
# ============================================================
def _read() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    default: dict[str, Any] = {"enabled": False, "messages": {}}
    if not STATE_FILE.exists():
        return default
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default
        messages = data.get("messages", {})
        if not isinstance(messages, dict):
            messages = {}
        return {
            "enabled": bool(data.get("enabled", False)),
            "messages": {str(k): int(v) for k, v in messages.items()},
        }
    except (OSError, ValueError, json.JSONDecodeError):
        logger.exception("Не удалось прочитать %s", STATE_FILE)
        return default


def _write(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def panel_enabled() -> bool:
    with _LOCK:
        return _read()["enabled"]


def set_panel_enabled(enabled: bool) -> None:
    with _LOCK:
        state = _read()
        state["enabled"] = bool(enabled)
        _write(state)


def get_panel_messages() -> dict[str, int]:
    with _LOCK:
        return dict(_read()["messages"])


def set_panel_message(symbol: str, message_id: int) -> None:
    with _LOCK:
        state = _read()
        state["messages"][symbol] = int(message_id)
        _write(state)


def drop_panel_message(symbol: str) -> None:
    with _LOCK:
        state = _read()
        state["messages"].pop(symbol, None)
        _write(state)


def clear_panel_messages() -> None:
    with _LOCK:
        state = _read()
        state["messages"] = {}
        _write(state)


# ============================================================
# RENDER: свечной график одной монеты + подпись с ценой
# ============================================================
def _fmt_price(price: float) -> str:
    return f"{price:.8g}"


def render_coin(symbol: str, candles: list[dict[str, Any]]) -> tuple[bytes, str]:
    if not candles:
        raise ValueError(f"Нет свечей для {symbol}")

    visible = candles[-CANDLES:]

    bg, fg, grid = "#131722", "#d1d4dc", "#2a2e39"
    up, down = "#26a69a", "#ef5350"

    highs = [float(c["high"]) for c in visible]
    lows = [float(c["low"]) for c in visible]
    price = float(visible[-1]["close"])

    last24 = visible[-24:]
    high24 = max(float(c["high"]) for c in last24)
    low24 = min(float(c["low"]) for c in last24)
    base_index = max(0, len(visible) - 25)
    base = float(visible[base_index]["close"])
    change = (price / base - 1.0) * 100.0 if base > 0 else 0.0
    line_color = up if change >= 0 else down

    span = (max(highs) - min(lows)) or max(price, 1.0) * 0.001

    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=100)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    for i, candle in enumerate(visible):
        opn = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        color = up if close >= opn else down
        ax.vlines(i, low, high, color=color, linewidth=0.8, zorder=2)
        body = abs(close - opn) or span * 0.0005
        ax.bar(i, body, bottom=min(opn, close), width=0.68,
               color=color, edgecolor=color, linewidth=0.4, zorder=3)

    n = len(visible)
    ax.axhline(price, color=line_color, linestyle="--", linewidth=1.0, zorder=4)
    ax.text(n - 0.3, price, f" {_fmt_price(price)}", color=line_color,
            va="center", ha="left", fontsize=9, fontweight="bold")

    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    ax.set_title(f"{symbol} · 1h · {now} UTC", color=fg, fontsize=11,
                 fontweight="bold", loc="left")
    ax.tick_params(axis="y", colors=fg, labelsize=8)
    ax.set_xticks([])
    ax.set_xlim(-1, n + max(6, n // 8))
    pad = span * 0.06
    ax.set_ylim(min(lows) - pad, max(highs) + pad)
    ax.grid(color=grid, linewidth=0.5, alpha=0.9)
    for spine in ax.spines.values():
        spine.set_color(grid)
    fig.tight_layout()

    output = BytesIO()
    fig.savefig(output, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)

    icon = "🟢" if change >= 0 else "🔴"
    caption = (
        f"{icon} {symbol}\n"
        f"💵 Цена: {_fmt_price(price)} USDT\n"
        f"📈 За 24ч: {change:+.2f}%\n"
        f"⬆️ Макс 24ч: {_fmt_price(high24)}\n"
        f"⬇️ Мин 24ч: {_fmt_price(low24)}\n"
        f"🕒 {now} UTC"
    )
    return output.getvalue(), caption
