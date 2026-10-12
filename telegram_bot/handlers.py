"""Telegram-команды MASB с сохранением владельца и повтором запросов."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

from telegram import Update
from telegram.constants import ChatType
from telegram.error import NetworkError
from telegram.ext import ContextTypes

from config import DATA_DIR
from telegram_bot.keyboards import main_menu
from telegram_bot.messages import (
    balance_message,
    help_message,
    status_message,
    strategies_message,
)

logger = logging.getLogger(__name__)

OWNER_FILE = DATA_DIR / "telegram_owner.json"
MAX_SEND_ATTEMPTS = 4


def load_owner_id() -> int | None:
    """Читает ID владельца. При повреждении файла безопасно останавливается."""
    if not OWNER_FILE.exists():
        return None

    try:
        data = json.loads(OWNER_FILE.read_text(encoding="utf-8"))
        owner_id = int(data["owner_user_id"])

        if owner_id <= 0:
            raise ValueError("Owner ID must be positive")

        return owner_id
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cannot read {OWNER_FILE}. Do not delete or reset it blindly."
        ) from exc


def save_owner_id(owner_id: int) -> None:
    """Атомарно сохраняет ID владельца."""
    if owner_id <= 0:
        raise ValueError("Owner ID must be positive")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_file = OWNER_FILE.with_suffix(".tmp")

    temporary_file.write_text(
        json.dumps({"owner_user_id": owner_id}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(OWNER_FILE)


async def _retry_telegram(
    operation: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any | None:
    """Повторяет Telegram-запрос при временных сетевых ошибках."""
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        try:
            return await operation(*args, **kwargs)
        except NetworkError:
            if attempt == MAX_SEND_ATTEMPTS:
                logger.exception(
                    "Telegram request failed after %s attempts.",
                    MAX_SEND_ATTEMPTS,
                )
                return None

            delay = min(2 ** (attempt - 1), 8)
            logger.warning(
                "Telegram connection error; retry %s/%s in %s sec.",
                attempt,
                MAX_SEND_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)

    return None


def _is_private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == ChatType.PRIVATE)


def _is_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    owner_id = context.application.bot_data.get("owner_user_id")

    return bool(
        _is_private_chat(update)
        and user
        and owner_id is not None
        and user.id == owner_id
    )


async def _deny(update: Update) -> None:
    message = update.effective_message
    if message:
        await _retry_telegram(
            message.reply_text,
            "⛔ Доступ запрещён. Управлять MASB может только владелец "
            "в личном чате с ботом.",
        )


async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    if not _is_private_chat(update):
        await _retry_telegram(
            message.reply_text,
            "Открой бота в личном чате, а не в группе.",
        )
        return

    owner_id = context.application.bot_data.get("owner_user_id")

    if owner_id is None:
        try:
            save_owner_id(user.id)
        except OSError:
            logger.exception("Could not save MASB owner.")
            await _retry_telegram(
                message.reply_text,
                "❌ Не удалось сохранить владельца. Проверь права на папку data.",
            )
            return

        context.application.bot_data["owner_user_id"] = user.id
        logger.warning("MASB owner registered. Telegram user ID: %s", user.id)

    elif not _is_authorized(update, context):
        await _deny(update)
        return

    # При первом запуске и при каждом следующем /start меню показывается одинаково.
    await _retry_telegram(
        message.reply_text,
        "👋 *Добро пожаловать в MASB!*\n\n"
        "Владелец зарегистрирован.\n"
        "Выбери действие в меню ниже.\n\n"
        "Пока доступны настройки и состояние. "
        "Рыночные сигналы и реальные сделки не реализованы.",
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


async def cmd_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update, context):
        await _deny(update)
        return

    if update.effective_message:
        await _retry_telegram(
            update.effective_message.reply_text,
            help_message(),
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )


async def cmd_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update, context):
        await _deny(update)
        return

    storage = context.application.bot_data["storage"]
    text = status_message(
        storage.load("config"),
        storage.load("realtime_state"),
    )

    if update.effective_message:
        await _retry_telegram(
            update.effective_message.reply_text,
            text,
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )


async def cmd_balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update, context):
        await _deny(update)
        return

    config = context.application.bot_data["storage"].load("config")

    if update.effective_message:
        await _retry_telegram(
            update.effective_message.reply_text,
            balance_message(config),
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )


async def cmd_strategies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not _is_authorized(update, context):
        await _deny(update)
        return

    config = context.application.bot_data["storage"].load("config")

    if update.effective_message:
        await _retry_telegram(
            update.effective_message.reply_text,
            strategies_message(config),
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )


async def button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query:
        return

    if not _is_authorized(update, context):
        await _retry_telegram(
            query.answer,
            "Доступ запрещён",
            show_alert=True,
        )
        return

    answer_result = await _retry_telegram(query.answer)
    if answer_result is None:
        logger.warning("Could not acknowledge Telegram button press.")

    storage = context.application.bot_data["storage"]
    action = query.data

    if action == "status":
        text = status_message(
            storage.load("config"),
            storage.load("realtime_state"),
        )
    elif action == "balance":
        text = balance_message(storage.load("config"))
    elif action == "strategies":
        text = strategies_message(storage.load("config"))
    else:
        text = help_message()

    await _retry_telegram(
        query.edit_message_text,
        text,
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )