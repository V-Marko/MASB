import json
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import DATA_DIR
from services.notification_settings import (
    notifications_enabled,
    toggle_notifications,
)
from services.signal_history import get_signals


logger = logging.getLogger(__name__)

STRATEGY_FILE = Path(DATA_DIR) / "strategy_manager.json"

BTN_TRADES = "📊 Сделки"
BTN_NOTIFICATIONS = "🔔 Уведомления"


def _load_strategies():
    if not STRATEGY_FILE.exists():
        return {}

    try:
        with STRATEGY_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return {}

        strategies = data.get("strategies", {})
        return strategies if isinstance(strategies, dict) else {}

    except (OSError, json.JSONDecodeError):
        logger.exception("Не удалось прочитать strategy_manager.json")
        return {}


def _format_price(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)

    return f"{value:.8f}".rstrip("0").rstrip(".")


async def cmd_trades(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if message is None:
        return

    signals = get_signals(limit=10)
    strategies = _load_strategies()

    lines = [
        "📊 MASB — сделки и сигналы",
        "",
        "ℹ️ История ниже содержит сигналы стратегии,",
        "а не подтверждённые исполнения ордеров.",
        "",
    ]

    if strategies:
        lines.append("🧩 Стратегии:")

        for name, info in strategies.items():
            if not isinstance(info, dict):
                continue

            enabled = bool(info.get("enabled", False))
            status = "🟢 Включена" if enabled else "⚪ Выключена"

            lines.append(
                f"• {name} — {status}"
                f" | сигналов: {info.get('signals', 0)}"
            )

            error = info.get("last_error")
            if error:
                lines.append(f"  ⚠️ Ошибка: {str(error)[:150]}")

        lines.append("")

    if not signals:
        lines.append("📭 Сохранённых сигналов пока нет.")
        lines.append("")
        lines.append("Когда стратегия найдёт сигнал, он появится здесь.")

        await message.reply_text("\n".join(lines))
        return

    lines.append("🕘 Последние сигналы:")

    for signal in signals:
        direction = str(signal.get("direction", "?")).upper()
        icon = "🟢" if direction == "LONG" else "🔴"

        lines.extend([
            "",
            (
                f"{icon} {signal.get('symbol', '?')} "
                f"{direction}"
            ),
            f"Стратегия: {signal.get('strategy', '?')}",
            f"Цена: {_format_price(signal.get('price', 0))}",
            f"Entry: {_format_price(signal.get('entry', 0))}",
            f"TP: {_format_price(signal.get('tp', 0))}",
            f"SL: {_format_price(signal.get('sl', 0))}",
            f"RR: 1:{signal.get('rr', 3)}",
            f"Время UTC: {signal.get('time', '?')}",
        ])

    # Telegram ограничивает длину сообщения.
    output = "\n".join(lines)

    if len(output) > 4000:
        output = output[:3900] + "\n\n… Список сокращён."

    await message.reply_text(output)


async def cmd_notifications(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if message is None:
        return

    enabled = toggle_notifications()

    if enabled:
        text = (
            "🔔 Уведомления включены.\n\n"
            "Новые сигналы будут отправляться в Telegram."
        )
    else:
        text = (
            "🔕 Уведомления выключены.\n\n"
            "Сканер продолжит работу, но новые сигналы "
            "не будут отправляться."
        )

    await message.reply_text(text)


async def notification_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await cmd_notifications(update, context)


async def trades_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await cmd_trades(update, context)


async def notification_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if message is None:
        return

    enabled = notifications_enabled()

    await message.reply_text(
        "🔔 Уведомления сейчас включены."
        if enabled
        else "🔕 Уведомления сейчас выключены."
    )


def register_trade_handlers(application):
    # Эти обработчики нужно зарегистрировать ДО общего
    # MessageHandler, который обрабатывает все текстовые кнопки.

    application.add_handler(
        CommandHandler("trades", cmd_trades)
    )

    application.add_handler(
        CommandHandler("notifications", cmd_notifications)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^📊 Сделки$"),
            trades_button,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^🔔 Уведомления$"),
            notification_button,
        )
    )