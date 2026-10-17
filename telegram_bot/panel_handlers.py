from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from services.binance_market import get_klines
from services.live_panel import (
    CANDLES,
    clear_panel_messages,
    drop_panel_message,
    get_panel_messages,
    panel_enabled,
    render_coin,
    set_panel_enabled,
    set_panel_message,
)
from services.strategy_manager import get_symbols
from telegram_bot.handlers import get_owner_id, send_tracked_message

logger = logging.getLogger("MASB.telegram.panel")

UPDATE_INTERVAL = 60   # секунд между полными обновлениями
SEND_DELAY = 1.1       # пауза между сообщениями, чтобы не упереться в лимиты Telegram
_REFRESH_LOCK = asyncio.Lock()


# ============================================================
# SETTINGS UI
# ============================================================
def settings_text() -> str:
    state = "🟢 ВКЛЮЧЕНА" if panel_enabled() else "⚪ ВЫКЛЮЧЕНА"
    return (
        "⚙️ Настройки MASB\n\n"
        f"📡 Живая панель: {state}\n\n"
        "Для каждой монеты — отдельное сообщение с её графиком и ценой. "
        f"Сообщения обновляются на месте каждые {UPDATE_INTERVAL} сек., "
        "новые не отправляются."
    )


def settings_keyboard() -> InlineKeyboardMarkup:
    label = "⏹ Выключить живую панель" if panel_enabled() else "🟢 Включить живую панель"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="panel:toggle")]])


async def show_settings_menu(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_tracked_message(
        chat_id,
        settings_text(),
        context,
        with_keyboard=False,
        reply_markup=settings_keyboard(),
    )


# ============================================================
# PANEL
# ============================================================
async def _push_coin(
    application: Application,
    chat_id: int,
    symbol: str,
    photo: bytes,
    caption: str,
) -> None:
    """Обновить сообщение монеты на месте; если его нет — отправить новое."""
    for attempt in range(2):
        message_id = get_panel_messages().get(symbol)
        try:
            if message_id is not None:
                try:
                    await application.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=message_id,
                        media=InputMediaPhoto(media=photo, caption=caption),
                    )
                    return
                except BadRequest as error:
                    if "not modified" in str(error).lower():
                        return
                    logger.warning("Сообщение %s недоступно, отправляю новое: %s", symbol, error)
                    drop_panel_message(symbol)

            sent = await application.bot.send_photo(chat_id=chat_id, photo=photo, caption=caption)
            set_panel_message(symbol, sent.message_id)
            return

        except RetryAfter as error:
            await asyncio.sleep(float(error.retry_after) + 1.0)
        except TelegramError as error:
            logger.warning("Ошибка Telegram для %s: %s", symbol, error)
            return


async def refresh_panel(application: Application, chat_id: int) -> None:
    async with _REFRESH_LOCK:
        symbols = get_symbols()

        # Удаляем сообщения монет, которых больше нет в списке.
        for symbol, message_id in list(get_panel_messages().items()):
            if symbol not in symbols:
                try:
                    await application.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except TelegramError:
                    pass
                drop_panel_message(symbol)

        for symbol in symbols:
            if not panel_enabled():
                return  # выключили посреди обновления
            try:
                candles = await asyncio.to_thread(get_klines, symbol, CANDLES, include_current=True)
                if not candles:
                    continue
                photo, caption = await asyncio.to_thread(render_coin, symbol, candles)
                await _push_coin(application, chat_id, symbol, photo, caption)
            except Exception:
                logger.exception("Не удалось обновить панель для %s", symbol)
            await asyncio.sleep(SEND_DELAY)


async def remove_panel(application: Application, chat_id: int) -> None:
    async with _REFRESH_LOCK:
        for symbol, message_id in list(get_panel_messages().items()):
            try:
                await application.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except RetryAfter as error:
                await asyncio.sleep(float(error.retry_after) + 1.0)
            except TelegramError:
                pass
            await asyncio.sleep(0.2)
        clear_panel_messages()


async def live_panel_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not panel_enabled():
        return
    owner = get_owner_id()
    if owner is None:
        return
    try:
        await refresh_panel(context.application, owner)
    except Exception:
        logger.exception("Ошибка обновления живой панели")


# ============================================================
# CALLBACK
# ============================================================
async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    owner = get_owner_id()
    if owner is None or update.effective_user is None or update.effective_user.id != owner:
        await query.answer("Доступ запрещён.", show_alert=True)
        return
    await query.answer()

    if query.data != "panel:toggle":
        return

    new_value = not panel_enabled()
    set_panel_enabled(new_value)

    # Долгие операции уходят в фон, чтобы бот не «зависал» на ~30 секунд.
    if new_value:
        context.application.create_task(refresh_panel(context.application, owner))
    else:
        context.application.create_task(remove_panel(context.application, owner))

    await query.edit_message_text(settings_text(), reply_markup=settings_keyboard())


# ============================================================
# REGISTER
# ============================================================
def register_panel_handlers(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(panel_callback, pattern=r"^panel:"))

    if application.job_queue is None:
        raise RuntimeError('JobQueue недоступен. Установи "python-telegram-bot[job-queue]".')

    application.job_queue.run_repeating(
        live_panel_job,
        interval=UPDATE_INTERVAL,
        first=20,
        name="masb_live_panel",
    )
    logger.info("Живая панель зарегистрирована.")
