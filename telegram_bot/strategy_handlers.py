
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from services.binance_market import get_klines
from services.strategy_manager import (
    add_strategy,
    get_last_candle,
    get_symbols,
    list_strategies,
    record_error,
    record_signal,
    set_last_candle,
    set_strategy_enabled,
)

from strategy import fibZZ10
from services.trade_monitor import (
    active_trades,
    add_signal_orders,
    all_trades,
    close_trade,
    entry_hit,
    render_trade_chart,
    trade_stats,
    touched_exit,
    update_trade,
)


logger = logging.getLogger("MASB.Strategies")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SETTINGS_FILE = DATA_DIR / "strategy_settings.json"

INITIAL_VIRTUAL_BALANCE = 100.0

# Добавляй новые стратегии сюда.
STRATEGIES: dict[str, Callable[..., Any]] = {
    "fibZZ10": fibZZ10.analyze,
}

STRATEGY_FORMATTERS = {
    "fibZZ10": fibZZ10.format_signal,
}


# ============================================================
# JSON SETTINGS
# ============================================================

def _read_settings() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not SETTINGS_FILE.exists():
        return {}

    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.exception("Не удалось прочитать %s", SETTINGS_FILE)
        return {}


def _write_settings(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    temp_path = SETTINGS_FILE.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(SETTINGS_FILE)


def notifications_enabled(strategy_name: str) -> bool:
    settings = _read_settings()
    strategy_settings = settings.get(strategy_name, {})

    if not isinstance(strategy_settings, dict):
        return True

    # Уведомления включены по умолчанию.
    return bool(strategy_settings.get("notifications_enabled", True))


def toggle_notifications(strategy_name: str) -> bool:
    settings = _read_settings()

    strategy_settings = settings.get(strategy_name, {})
    if not isinstance(strategy_settings, dict):
        strategy_settings = {}

    new_value = not bool(
        strategy_settings.get("notifications_enabled", True)
    )

    strategy_settings["notifications_enabled"] = new_value
    settings[strategy_name] = strategy_settings

    _write_settings(settings)
    return new_value


# ============================================================
# OWNER ACCESS
# ============================================================

def _owner_id() -> int | None:
    path = DATA_DIR / "telegram_owner.json"

    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))

        if isinstance(data, int):
            return data

        if isinstance(data, str) and data.isdigit():
            return int(data)

        if isinstance(data, dict):
            for key in ("owner_id", "user_id", "telegram_id", "id"):
                value = data.get(key)
                if value is not None:
                    return int(value)

    except (OSError, ValueError, TypeError):
        logger.exception("Не удалось прочитать владельца Telegram")

    return None


async def _authorized(update: Update) -> bool:
    owner = _owner_id()
    user = update.effective_user

    if owner is not None and user is not None and user.id == owner:
        return True

    message = update.effective_message

    if message is not None:
        await message.reply_text(
            "⛔ Управлять MASB может только владелец бота."
        )

    return False


# ============================================================
# INLINE KEYBOARDS
# ============================================================

def strategies_keyboard() -> InlineKeyboardMarkup:
    states = list_strategies()
    rows = []

    for name, info in states.items():
        status = "🟢" if info.get("enabled") else "⚪"

        rows.append([
            InlineKeyboardButton(
                f"{status} {name}",
                callback_data=f"masb:open:{name}",
            )
        ])

    if not rows:
        rows.append([
            InlineKeyboardButton(
                "Стратегии не найдены",
                callback_data="masb:noop",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "🔄 Обновить список",
            callback_data="masb:list",
        )
    ])

    return InlineKeyboardMarkup(rows)


def strategy_keyboard(name: str) -> InlineKeyboardMarkup:
    states = list_strategies()
    info = states.get(name, {})
    enabled = bool(info.get("enabled", False))
    notify = notifications_enabled(name)

    toggle_label = (
        "⏹ Выключить стратегию"
        if enabled
        else "🟢 Включить стратегию"
    )

    notify_label = (
        "🔔 Уведомления: ВКЛ"
        if notify
        else "🔕 Уведомления: ВЫКЛ"
    )

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                toggle_label,
                callback_data=f"masb:toggle:{name}",
            )
        ],
        [
            InlineKeyboardButton(
                notify_label,
                callback_data=f"masb:notify:{name}",
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Баланс",
                callback_data=f"masb:balance:{name}",
            ),
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data=f"masb:stats:{name}",
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад к стратегиям",
                callback_data="masb:list",
            )
        ],
    ])


# ============================================================
# TEXT GENERATORS
# ============================================================

def strategies_text() -> str:
    states = list_strategies()

    if not states:
        return (
            "🧩 Стратегии MASB\n\n"
            "Стратегии пока не зарегистрированы."
        )

    lines = [
        "🧩 Стратегии MASB",
        "",
        "Выбери стратегию кнопкой ниже.",
        "",
    ]

    for name, info in states.items():
        status = (
            "🟢 Включена"
            if info.get("enabled")
            else "⚪ Выключена"
        )

        lines.extend([
            f"📌 {name}",
            f"Статус: {status}",
            f"Сигналов: {info.get('signals', 0)}",
            "",
        ])

    return "\n".join(lines).strip()


def strategy_details_text(name: str) -> str:
    states = list_strategies()
    info = states.get(name)

    if info is None:
        return f"Стратегия {name} не найдена."

    enabled = bool(info.get("enabled", False))
    status = "🟢 ВКЛЮЧЕНА" if enabled else "⚪ ВЫКЛЮЧЕНА"
    notify = notifications_enabled(name)
    notify_status = "ВКЛЮЧЕНЫ" if notify else "ВЫКЛЮЧЕНЫ"

    lines = [
        f"📌 Стратегия: {name}",
        "",
        f"Статус: {status}",
        f"Сигналов обнаружено: {info.get('signals', 0)}",
        f"Уведомления: {notify_status}",
        "",
        "📈 Рынок: Binance USDⓈ-M Futures",
        "⏱ Таймфрейм: 1 час",
        "🧪 Режим: виртуальные сделки",
        "💵 Стартовый виртуальный баланс: "
        f"{INITIAL_VIRTUAL_BALANCE:.2f} USDT",
        "",
        "Графики: Entry оранжевый, TP зелёный, SL красный.",
        "Если одна свеча задевает TP и SL, считается SL.",
        "Реальные ордера отключены.",
    ]

    error = info.get("last_error")
    if error:
        lines.extend([
            "",
            f"⚠️ Последняя ошибка: {str(error)[:300]}",
        ])

    return "\n".join(lines)


def strategy_balance_text(name: str) -> str:
    states = list_strategies()
    if name not in states:
        return f"Стратегия {name} не найдена."

    stats = trade_stats(name)
    return (
        f"💰 Виртуальный баланс {name}\n\n"
        f"Стартовый баланс: {INITIAL_VIRTUAL_BALANCE:.2f} USDT\n"
        f"Текущий баланс: {stats['balance']:.4f} USDT\n"
        f"Реализованный PnL (после комиссий): {stats['pnl_net']:+.4f} USDT\n\n"
        f"Ожидают Entry: {stats['pending']}\n"
        f"Открытые позиции: {stats['open']}\n"
        f"Закрытые сделки: {stats['closed']}\n\n"
        "Это виртуальный учёт; реальные ордера не отправляются."
    )


def strategy_stats_text(name: str) -> str:
    states = list_strategies()
    info = states.get(name)
    if info is None:
        return f"Стратегия {name} не найдена."

    error = info.get("last_error")
    error_text = str(error)[:300] if error else "нет"
    stats = trade_stats(name)
    return (
        f"📊 Статистика {name}\n\n"
        f"Статус: {'🟢 Включена' if info.get('enabled') else '⚪ Выключена'}\n"
        f"Обнаружено сигналов: {info.get('signals', 0)}\n"
        f"Ожидают Entry: {stats['pending']}\n"
        f"Открытые сделки: {stats['open']}\n"
        f"Закрытые сделки: {stats['closed']}\n"
        f"TP: {stats['wins']} | SL: {stats['losses']}\n"
        f"Win rate: {stats['win_rate']:.1f}%\n"
        f"PnL net: {stats['pnl_net']:+.4f} USDT\n"
        f"Виртуальный баланс: {stats['balance']:.4f} USDT\n"
        f"Уведомления: {'ВКЛ' if notifications_enabled(name) else 'ВЫКЛ'}\n"
        f"Последняя ошибка: {error_text}\n\n"
        "Если одна свеча касается TP и SL, результат считается по SL.\n"
        "Реальные ордера отключены."
    )


# ============================================================
# SHOW STRATEGIES MENU
# ============================================================

async def show_strategies_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    chat = update.effective_chat

    if chat is None:
        return

    # Используем существующую функцию отслеживания сообщений.
    from telegram_bot.handlers import send_tracked_message
    from telegram_bot.keyboards import main_keyboard

    # Сначала показываем inline-панель стратегий.
    await send_tracked_message(
        chat_id=chat.id,
        text=strategies_text(),
        context=context,
        with_keyboard=False,
        reply_markup=strategies_keyboard(),
    )

    # Отдельное сообщение повторно показывает постоянную ReplyKeyboard.
    # Это важно, потому что экран стратегий использует inline-кнопки.
    await send_tracked_message(
        chat_id=chat.id,
        text="📌 Основное меню MASB закреплено снизу. Кнопки доступны в любой момент.",
        context=context,
        with_keyboard=False,
        reply_markup=main_keyboard(),
    )


# ============================================================
# CALLBACK BUTTON HANDLER
# ============================================================

async def strategy_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    if not await _authorized(update):
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    await query.answer()

    data = query.data or ""

    if data == "masb:noop":
        return

    if data == "masb:list":
        await query.edit_message_text(
            text=strategies_text(),
            reply_markup=strategies_keyboard(),
        )
        return

    parts = data.split(":", maxsplit=2)

    if len(parts) != 3 or parts[0] != "masb":
        return

    action, name = parts[1], parts[2]

    if name not in STRATEGIES:
        await query.edit_message_text(
            f"Стратегия {name} не зарегистрирована."
        )
        return

    if action == "open":
        await query.edit_message_text(
            text=strategy_details_text(name),
            reply_markup=strategy_keyboard(name),
        )
        return

    if action == "toggle":
        states = list_strategies()
        currently_enabled = bool(
            states.get(name, {}).get("enabled", False)
        )

        try:
            set_strategy_enabled(name, not currently_enabled)
        except Exception as exc:
            logger.exception("Не удалось изменить состояние %s", name)
            await query.edit_message_text(
                f"❌ Не удалось изменить состояние стратегии: {exc}",
                reply_markup=strategy_keyboard(name),
            )
            return

        await query.edit_message_text(
            text=strategy_details_text(name),
            reply_markup=strategy_keyboard(name),
        )
        return

    if action == "notify":
        try:
            toggle_notifications(name)
        except Exception as exc:
            logger.exception("Не удалось изменить уведомления %s", name)
            await query.edit_message_text(
                f"❌ Не удалось изменить уведомления: {exc}",
                reply_markup=strategy_keyboard(name),
            )
            return

        await query.edit_message_text(
            text=strategy_details_text(name),
            reply_markup=strategy_keyboard(name),
        )
        return

    if action == "balance":
        await query.edit_message_text(
            text=strategy_balance_text(name),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ К стратегии",
                        callback_data=f"masb:open:{name}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ К списку стратегий",
                        callback_data="masb:list",
                    )
                ],
            ]),
        )
        return

    if action == "stats":
        await query.edit_message_text(
            text=strategy_stats_text(name),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 Обновить",
                        callback_data=f"masb:stats:{name}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ К стратегии",
                        callback_data=f"masb:open:{name}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ К списку стратегий",
                        callback_data="masb:list",
                    )
                ],
            ]),
        )


# ============================================================
# OPTIONAL COMMANDS — KEPT FOR COMPATIBILITY
# ============================================================

async def cmd_strategies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await _authorized(update):
        return

    await show_strategies_menu(update, context)


async def cmd_strategy_on(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await _authorized(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Выбери стратегию через меню 🧩 Стратегии."
        )
        return

    name = context.args[0].strip()

    if name not in STRATEGIES:
        await update.effective_message.reply_text(
            f"Стратегия {name} не зарегистрирована."
        )
        return

    set_strategy_enabled(name, True)
    await update.effective_message.reply_text(
        f"🟢 Стратегия {name} включена."
    )


async def cmd_strategy_off(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await _authorized(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Выбери стратегию через меню 🧩 Стратегии."
        )
        return

    name = context.args[0].strip()

    try:
        set_strategy_enabled(name, False)
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    await update.effective_message.reply_text(
        f"⏹ Стратегия {name} выключена."
    )


async def cmd_strategy_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not await _authorized(update):
        return

    states = list_strategies()
    active = [
        name for name, info in states.items()
        if info.get("enabled")
    ]

    await update.effective_message.reply_text(
        "📊 Состояние MASB\n\n"
        f"Всего стратегий: {len(states)}\n"
        f"Активных: {len(active)}\n"
        f"Активные: {', '.join(active) if active else 'нет'}\n\n"
        "Источник данных: Binance Futures\n"
        "Таймфрейм: 1h\n"
        "Реальные ордера отключены."
    )


# ============================================================
# SCANNER
# ============================================================

async def _event_chart_candles(
    symbol: str,
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        history = await asyncio.to_thread(get_klines, symbol, 180, include_current=True)
        return history or fallback
    except Exception:
        logger.exception("Не удалось загрузить расширенную историю для графика %s", symbol)
        return fallback


async def _send_trade_chart(
    application: Application,
    trade: dict[str, Any],
    candles: list[dict[str, Any]],
    phase: str,
    caption: str,
) -> None:
    owner = _owner_id()
    if owner is None or not notifications_enabled(str(trade.get("strategy", "fibZZ10"))):
        return
    try:
        chart = await asyncio.to_thread(render_trade_chart, candles, trade, phase)
        await application.bot.send_photo(
            chat_id=owner,
            photo=chart,
            caption=caption[:1000],
        )
    except Exception:
        logger.exception("Не удалось отправить график %s / %s", trade.get("symbol"), phase)
        try:
            await application.bot.send_message(chat_id=owner, text=caption[:3500])
        except Exception:
            logger.exception("Не удалось отправить резервное сообщение о сделке")


async def _scan_strategy(
    application: Application,
    name: str,
    analyzer: Callable[..., Any],
) -> None:
    symbols = get_symbols()

    for symbol in symbols:
        try:
            candles = await asyncio.to_thread(get_klines, symbol, 500)
            if len(candles) < 30:
                continue

            last_candle = candles[-1]
            timestamp = int(last_candle["timestamp"])
            previous_timestamp = get_last_candle(name, symbol)
            if previous_timestamp == timestamp:
                continue

            pending = len([t for t in active_trades(name, symbol) if t.get("status") == "PENDING"])
            opened = len([t for t in active_trades(name, symbol) if t.get("status") == "OPEN"])
            signal = await asyncio.to_thread(
                analyzer,
                candles,
                current_price=float(last_candle["close"]),
                capital=INITIAL_VIRTUAL_BALANCE,
                open_positions=opened,
                pending_orders=pending,
            )
            set_last_candle(name, symbol, timestamp)
            if signal is None or not signal.get("orders"):
                continue

            new_trades = add_signal_orders(name, symbol, signal, timestamp)
            if not new_trades:
                continue

            record_signal(name)
            for trade in new_trades:
                caption = (
                    f"🔔 НОВЫЙ СИГНАЛ | {symbol}\n"
                    f"Стратегия: {name} | ZigZag {signal.get('zigzag_deviation', 10)}% + Fibonacci\n"
                    f"Направление: {trade['direction']} | Сделка №{trade['entry_number']}\n"
                    f"🟠 Entry: {trade['entry']:.8g} (Fib {trade['entry_fib']})\n"
                    f"🟢 TP: {trade['tp']:.8g}\n"
                    f"🔴 SL: {trade['sl']:.8g}\n"
                    "Статус: ожидает касания Entry. Виртуальная сделка."
                )
                await _send_trade_chart(application, trade, candles, "SIGNAL", caption)

        except Exception as exc:
            logger.exception("Ошибка сканирования %s / %s", name, symbol)
            try:
                record_error(name, f"{symbol}: {exc}")
            except Exception:
                logger.exception("Не удалось записать ошибку стратегии")


async def monitor_active_trades(context: ContextTypes.DEFAULT_TYPE) -> None:
    application = context.application
    trades = active_trades()
    if not trades:
        return

    symbols = sorted({str(t["symbol"]) for t in trades})
    candle_cache: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        try:
            candle_cache[symbol] = await asyncio.to_thread(
                get_klines, symbol, 30, include_current=True
            )
        except Exception:
            logger.exception("Не удалось обновить свечи для мониторинга %s", symbol)

    for original in trades:
        trade = original
        candles = candle_cache.get(str(trade["symbol"]), [])
        if not candles:
            continue
        signal_ts = int(trade.get("signal_candle_timestamp") or 0)
        last_checked = trade.get("last_checked_candle")
        relevant = [
            c for c in candles
            if int(c.get("timestamp", 0)) > signal_ts
            and (last_checked is None or int(c.get("timestamp", 0)) >= int(last_checked))
        ]
        for candle in relevant:
            candle_ts = int(candle.get("timestamp", 0))
            if trade["status"] == "PENDING" and entry_hit(trade, candle):
                trade = update_trade(trade["id"], {
                    "status": "OPEN",
                    "entry_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "last_checked_candle": candle_ts,
                    "actual_entry": float(trade["entry"]),
                }) or trade
                caption = (
                    f"🟠 ВХОД СОСТОЯЛСЯ | {trade['symbol']}\n"
                    f"Стратегия: {trade['strategy']} | {trade['direction']}\n"
                    f"Entry: {trade['entry']:.8g}\n🟢 TP: {trade['tp']:.8g}\n🔴 SL: {trade['sl']:.8g}\n"
                    "Виртуальная позиция открыта."
                )
                chart_candles = await _event_chart_candles(str(trade["symbol"]), candles)
                await _send_trade_chart(application, trade, chart_candles, "ENTRY", caption)
            elif trade["status"] == "PENDING":
                trade = update_trade(trade["id"], {"last_checked_candle": candle_ts}) or trade

            if trade["status"] == "OPEN":
                outcome = touched_exit(trade, candle)
                if outcome:
                    exit_price = float(trade["sl"] if outcome == "SL" else trade["tp"])
                    updates = close_trade(trade, outcome, exit_price)
                    updates["last_checked_candle"] = candle_ts
                    trade = update_trade(trade["id"], updates) or {**trade, **updates}
                    emoji = "✅" if outcome == "TP" else "❌"
                    caption = (
                        f"{emoji} {outcome} | {trade['symbol']}\n"
                        f"Стратегия: {trade['strategy']} | {trade['direction']}\n"
                        f"Entry: {trade['entry']:.8g} | Exit: {exit_price:.8g}\n"
                        f"PnL gross: {float(trade.get('pnl_gross') or 0):+.4f} USDT\n"
                        f"Комиссии: {float(trade.get('fees') or 0):.4f} USDT\n"
                        f"PnL net: {float(trade.get('pnl_net') or 0):+.4f} USDT\n"
                        "Результат виртуальной сделки."
                    )
                    # TP and SL always get their own final chart with an EXIT marker.
                    chart_candles = await _event_chart_candles(str(trade["symbol"]), candles)
                    await _send_trade_chart(application, trade, chart_candles, outcome, caption)
                    break
                trade = update_trade(trade["id"], {"last_checked_candle": candle_ts}) or trade


async def scan_enabled_strategies(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    application = context.application

    try:
        states = list_strategies()

        for name, info in states.items():
            if not info.get("enabled"):
                continue

            analyzer = STRATEGIES.get(name)

            if analyzer is None:
                record_error(
                    name,
                    "Стратегия включена, но не зарегистрирована в STRATEGIES.",
                )
                continue

            await _scan_strategy(application, name, analyzer)

    except Exception:
        logger.exception("Ошибка фонового сканирования MASB")


# ============================================================
# REGISTER
# ============================================================

def register_strategy_handlers(application: Application) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing = list_strategies()

    for name in STRATEGIES:
        if name not in existing:
            add_strategy(name)

    application.add_handler(
        CommandHandler("strategies", cmd_strategies)
    )
    application.add_handler(
        CommandHandler("strategy_on", cmd_strategy_on)
    )
    application.add_handler(
        CommandHandler("strategy_off", cmd_strategy_off)
    )
    application.add_handler(
        CommandHandler("strategy_status", cmd_strategy_status)
    )
    application.add_handler(
        CallbackQueryHandler(
            strategy_callback,
            pattern=r"^masb:",
        )
    )

    if application.job_queue is None:
        raise RuntimeError(
            'JobQueue недоступен. Установи '
            '"python-telegram-bot[job-queue]" '
            'в активное виртуальное окружение.'
        )

    application.job_queue.run_repeating(
        scan_enabled_strategies,
        interval=60,
        first=10,
        name="masb_strategy_scanner",
    )
    application.job_queue.run_repeating(
        monitor_active_trades,
        interval=60,
        first=15,
        name="masb_trade_monitor",
    )

    logger.info("Менеджер стратегий и монитор виртуальных сделок зарегистрированы.")
