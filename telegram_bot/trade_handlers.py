import json
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import DATA_DIR
from services.notification_settings import (
    notifications_enabled,
    toggle_notifications,
)
from services.signal_history import get_signals
from services.binance_futures import (
    get_real_order_history,
    submit_manual_order,
    real_trading_enabled,
    BinanceFuturesError,
)
from services.strategy_manager import get_symbols


logger = logging.getLogger(__name__)

STRATEGY_FILE = Path(DATA_DIR) / "strategy_manager.json"

BTN_TRADES = "📊 Сделки"
BTN_NOTIFICATIONS = "🔔 Уведомления"

# Conversation states for manual Trade
(
    MT_SELECT_SYMBOL,
    MT_SELECT_DIRECTION,
    MT_ENTRY,
    MT_TP,
    MT_SL,
    MT_LEVERAGE,
    MT_MARGIN,
    MT_CONFIRM,
) = range(8)


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

    for signal in signals:
        if not isinstance(signal, dict):
            continue

        lines.append(
            f"• {signal.get('symbol', '?')} {signal.get('direction', '?')}"
            f" | {signal.get('strategy', '?')}"
        )
        lines.append(
            f"  Entry: {_format_price(signal.get('entry', 0))}"
            f" | TP: {_format_price(signal.get('tp', 0))}"
            f" | SL: {_format_price(signal.get('sl', 0))}"
        )

    real_orders = get_real_order_history()
    if real_orders:
        lines.extend(["", "🔴 REAL Binance Futures — последние заявки:"])
        for trade in real_orders[-10:][::-1]:
            status = str(trade.get("status", "?"))
            status_icon = "⏳" if status == "PENDING" else ("🟢" if status == "OPEN" else "⚫")
            lines.append(
                f"{status_icon} {trade.get('symbol', '?')} {trade.get('direction', '?')} — {status}"
            )
            lines.append(
                f"  Entry: {_format_price(trade.get('entry', 0))} | TP: {_format_price(trade.get('tp', 0))} | SL: {_format_price(trade.get('sl', 0))}"
            )
            lines.append(
                f"  Qty: {trade.get('quantity', '?')} | Leverage: {trade.get('leverage', '?')}x"
                f" | Risk est.: {float(trade.get('estimated_risk_with_fees') or 0):.4f} USDT"
            )
            lines.append(f"  Order ID: {trade.get('entry_order_id', '?')}")

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

    enabled = notifications_enabled("global")
    new_state = not enabled
    toggle_notifications("global")

    await message.reply_text(
        "🔔 Уведомления включены."
        if new_state
        else "🔕 Уведомления сейчас выключены."
    )


async def trades_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await cmd_trades(update, context)


async def notification_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await cmd_notifications(update, context)


# ============================================================
# MANUAL TRADE (🔴 Trade)
# ============================================================
def _symbols_keyboard(page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    symbols = get_symbols() or []
    symbols = sorted(symbols)
    start = page * per_page
    chunk = symbols[start : start + per_page]
    rows = []
    for i in range(0, len(chunk), 2):
        row = []
        for sym in chunk[i : i + 2]:
            short = sym.replace("USDT", "")
            row.append(
                InlineKeyboardButton(
                    short,
                    callback_data=f"mt:sym:{sym}",
                )
            )
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"mt:page:{page - 1}"))
    if start + per_page < len(symbols):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"mt:page:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="mt:cancel")])
    return InlineKeyboardMarkup(rows)


def _direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟢 LONG", callback_data="mt:dir:LONG"),
                InlineKeyboardButton("🔴 SHORT", callback_data="mt:dir:SHORT"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="mt:cancel")],
        ]
    )


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Отправить на Binance", callback_data="mt:confirm"),
                InlineKeyboardButton("❌ Отмена", callback_data="mt:cancel"),
            ]
        ]
    )


async def start_manual_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return ConversationHandler.END

    if not real_trading_enabled():
        await message.reply_text(
            "🔴 REAL-торговля сейчас **выключена**.\n\n"
            "В файле `.env` установите:\n"
            "`MASB_REAL_TRADING_ENABLED=true`\n\n"
            "И перезапустите бота. Без этого ручные сделки на аккаунт не отправляются.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data.pop("mt", None)
    context.user_data["mt"] = {}

    await message.reply_text(
        "🔴 **Trade** — ручная сделка на Binance Futures\n\n"
        "Выберите монету из вашего списка:",
        reply_markup=_symbols_keyboard(0),
        parse_mode="Markdown",
    )
    return MT_SELECT_SYMBOL


async def mt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return ConversationHandler.END

    await query.answer()
    data = query.data or ""

    if data == "mt:cancel":
        context.user_data.pop("mt", None)
        await query.edit_message_text("Отменено.")
        return ConversationHandler.END

    if data.startswith("mt:page:"):
        try:
            page = int(data.split(":")[-1])
        except ValueError:
            page = 0
        await query.edit_message_text(
            "🔴 **Trade** — выберите монету:",
            reply_markup=_symbols_keyboard(page),
            parse_mode="Markdown",
        )
        return MT_SELECT_SYMBOL

    if data.startswith("mt:sym:"):
        symbol = data.split(":", 2)[-1]
        context.user_data.setdefault("mt", {})["symbol"] = symbol
        await query.edit_message_text(
            f"Монета: **{symbol}**\n\nВыберите направление:",
            reply_markup=_direction_keyboard(),
            parse_mode="Markdown",
        )
        return MT_SELECT_DIRECTION

    if data.startswith("mt:dir:"):
        direction = data.split(":")[-1]
        context.user_data.setdefault("mt", {})["direction"] = direction
        await query.edit_message_text(
            f"Монета: **{context.user_data['mt']['symbol']}**\n"
            f"Направление: **{direction}**\n\n"
            "Введите **Entry** (цена входа) числом, например `0.4521` или `95000`:",
            parse_mode="Markdown",
        )
        return MT_ENTRY

    if data == "mt:confirm":
        mt = context.user_data.get("mt") or {}
        required = ("symbol", "direction", "entry", "tp", "sl", "leverage", "margin")
        if not all(k in mt for k in required):
            await query.edit_message_text("Данные сделки неполные. Начните заново: 🔴 Trade")
            context.user_data.pop("mt", None)
            return ConversationHandler.END

        await query.edit_message_text("⏳ Отправляю ордер на Binance…")

        try:
            record = submit_manual_order(
                symbol=mt["symbol"],
                direction=mt["direction"],
                entry=float(mt["entry"]),
                tp=float(mt["tp"]),
                sl=float(mt["sl"]),
                leverage=int(mt["leverage"]),
                margin_usdt=float(mt["margin"]),
            )
        except BinanceFuturesError as exc:
            await query.edit_message_text(f"❌ Ошибка Binance:\n{exc}")
            context.user_data.pop("mt", None)
            return ConversationHandler.END
        except Exception as exc:
            logger.exception("Manual trade failed")
            await query.edit_message_text(f"❌ Неожиданная ошибка:\n{exc}")
            context.user_data.pop("mt", None)
            return ConversationHandler.END

        context.user_data.pop("mt", None)
        protect = "SL/TP будут поставлены после исполнения входа" if record.get("protect_pending") else "SL/TP уже на бирже"
        await query.edit_message_text(
            "✅ Ордер отправлен на Binance Futures\n\n"
            f"Символ: {record.get('symbol')}\n"
            f"Направление: {record.get('direction')}\n"
            f"Entry: {_format_price(record.get('entry'))}\n"
            f"TP: {_format_price(record.get('tp'))}\n"
            f"SL: {_format_price(record.get('sl'))}\n"
            f"Qty: {record.get('quantity')}\n"
            f"Плечо: {record.get('leverage')}x\n"
            f"Маржа: {record.get('margin_usdt')} USDT\n"
            f"Номинал: {record.get('notional')} USDT\n"
            f"Risk est.: {float(record.get('estimated_risk_with_fees') or 0):.4f} USDT\n"
            f"Order ID: {record.get('entry_order_id')}\n"
            f"Статус: {record.get('status')}\n"
            f"{protect}"
        )
        return ConversationHandler.END

    return ConversationHandler.END


async def mt_entry_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return MT_ENTRY

    text = message.text.strip().replace(",", ".")
    try:
        entry = float(text)
        if entry <= 0:
            raise ValueError
    except ValueError:
        await message.reply_text("Некорректная цена. Введите положительное число (Entry):")
        return MT_ENTRY

    context.user_data.setdefault("mt", {})["entry"] = entry
    await message.reply_text(
        f"Entry: **{_format_price(entry)}**\n\nВведите **Take Profit (TP)**:",
        parse_mode="Markdown",
    )
    return MT_TP


async def mt_tp_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return MT_TP

    text = message.text.strip().replace(",", ".")
    try:
        tp = float(text)
        if tp <= 0:
            raise ValueError
    except ValueError:
        await message.reply_text("Некорректная цена. Введите положительное число (TP):")
        return MT_TP

    context.user_data.setdefault("mt", {})["tp"] = tp
    await message.reply_text(
        f"TP: **{_format_price(tp)}**\n\nВведите **Stop Loss (SL)**:",
        parse_mode="Markdown",
    )
    return MT_SL


async def mt_sl_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return MT_SL

    text = message.text.strip().replace(",", ".")
    try:
        sl = float(text)
        if sl <= 0:
            raise ValueError
    except ValueError:
        await message.reply_text("Некорректная цена. Введите положительное число (SL):")
        return MT_SL

    context.user_data.setdefault("mt", {})["sl"] = sl
    await message.reply_text(
        f"SL: **{_format_price(sl)}**\n\n"
        "Введите **плечо** (leverage) целым числом от 1 до 20, например `5` или `10`:",
        parse_mode="Markdown",
    )
    return MT_LEVERAGE


async def mt_leverage_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return MT_LEVERAGE

    text = message.text.strip()
    try:
        lev = int(float(text))
        if lev < 1 or lev > 50:
            raise ValueError
    except ValueError:
        await message.reply_text("Плечо должно быть целым числом от 1 до 50. Попробуйте снова:")
        return MT_LEVERAGE

    mt = context.user_data.setdefault("mt", {})
    mt["leverage"] = lev

    await message.reply_text(
        f"Плечо: **{lev}x**\n\n"
        "Введите **маржу в USDT** — сколько своих денег ставите в сделку.\n"
        "Пример: `10` или `25.5`\n\n"
        "Номинал позиции = маржа × плечо.\n"
        "Например: маржа 20 USDT при плече 10x → позиция ~200 USDT.",
        parse_mode="Markdown",
    )
    return MT_MARGIN



async def mt_margin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return MT_MARGIN

    raw = message.text.strip().replace(",", ".")
    try:
        margin = float(raw)
        if margin < 1:
            raise ValueError
    except ValueError:
        await message.reply_text("Маржа должна быть числом ≥ 1 USDT. Пример: `10`")
        return MT_MARGIN

    mt = context.user_data.setdefault("mt", {})
    mt["margin"] = margin

    direction = mt.get("direction", "")
    entry = float(mt["entry"])
    tp = float(mt["tp"])
    sl = float(mt["sl"])
    lev = int(mt["leverage"])
    notional = margin * lev

    warn = ""
    if direction == "LONG" and not (sl < entry < tp):
        warn = "\n⚠️ Для LONG обычно ожидается SL < Entry < TP"
    if direction == "SHORT" and not (tp < entry < sl):
        warn = "\n⚠️ Для SHORT обычно ожидается TP < Entry < SL"

    text = (
        "📋 **Подтверждение сделки**\n\n"
        f"Монета: `{mt['symbol']}`\n"
        f"Направление: **{direction}**\n"
        f"Entry: `{_format_price(entry)}`\n"
        f"TP: `{_format_price(tp)}`\n"
        f"SL: `{_format_price(sl)}`\n"
        f"Плечо: **{lev}x**\n"
        f"Маржа: **{margin:.4g} USDT**\n"
        f"Номинал ≈ **{notional:.2f} USDT**\n"
        f"{warn}\n\n"
        "Нажмите ✅ чтобы отправить LIMIT + SL/TP на ваш Binance Futures."
    )
    await message.reply_text(
        text,
        reply_markup=_confirm_keyboard(),
        parse_mode="Markdown",
    )
    return MT_CONFIRM


async def mt_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("mt", None)
    message = update.effective_message
    if message:
        await message.reply_text("Отменено.")
    return ConversationHandler.END


def register_trade_handlers(application: Application):
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

    from telegram_bot.keyboards import BTN_TRADE

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.TEXT & filters.Regex(f"^{BTN_TRADE}$"),
                start_manual_trade,
            ),
        ],
        states={
            MT_SELECT_SYMBOL: [
                CallbackQueryHandler(mt_callback, pattern=r"^mt:"),
            ],
            MT_SELECT_DIRECTION: [
                CallbackQueryHandler(mt_callback, pattern=r"^mt:"),
            ],
            MT_ENTRY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mt_entry_text),
                CallbackQueryHandler(mt_callback, pattern=r"^mt:"),
            ],
            MT_TP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mt_tp_text),
                CallbackQueryHandler(mt_callback, pattern=r"^mt:"),
            ],
            MT_SL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mt_sl_text),
                CallbackQueryHandler(mt_callback, pattern=r"^mt:"),
            ],
            MT_LEVERAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mt_leverage_text),
                CallbackQueryHandler(mt_callback, pattern=r"^mt:"),
            ],
            MT_MARGIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mt_margin_text),
                CallbackQueryHandler(mt_callback, pattern=r"^mt:"),
            ],
            MT_CONFIRM: [
                CallbackQueryHandler(mt_callback, pattern=r"^mt:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", mt_cancel_cmd),
            CallbackQueryHandler(mt_callback, pattern=r"^mt:cancel$"),
        ],
        name="manual_trade",
        persistent=False,
        allow_reentry=True,
    )
    application.add_handler(conv, group=0)
