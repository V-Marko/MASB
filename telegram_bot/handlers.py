import json
import logging
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_bot.keyboards import (
    BTN_STATUS,
    BTN_BALANCE,
    BTN_STRATEGIES,
    BTN_HELP,
    BTN_HOME,
    MAIN_MENU_BUTTONS,
    main_keyboard,
)

logger = logging.getLogger("MASB.telegram.handlers")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

OWNER_FILE = DATA_DIR / "telegram_owner.json"
MESSAGES_FILE = DATA_DIR / "telegram_messages.json"


# ============================================================
# JSON STORAGE
# ============================================================

def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default

        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    except (OSError, json.JSONDecodeError):
        logger.exception("Не удалось прочитать JSON: %s", path)
        return default


def write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    temp_path.replace(path)


# ============================================================
# OWNER
# ============================================================

def get_owner_id() -> int | None:
    data = read_json(OWNER_FILE, {})

    if not isinstance(data, dict):
        return None

    owner_id = data.get("owner_id")

    try:
        return int(owner_id) if owner_id is not None else None
    except (TypeError, ValueError):
        return None


def register_owner(user_id: int) -> None:
    write_json(
        OWNER_FILE,
        {"owner_id": user_id},
    )


def is_owner(update: Update) -> bool:
    if update.effective_user is None:
        return False

    owner_id = get_owner_id()

    return owner_id is not None and update.effective_user.id == owner_id


# ============================================================
# MESSAGE TRACKING
# ============================================================

def get_chat_messages(chat_id: int) -> list[int]:
    data = read_json(MESSAGES_FILE, {})

    if not isinstance(data, dict):
        return []

    messages = data.get(str(chat_id), [])

    if not isinstance(messages, list):
        return []

    result = []

    for message_id in messages:
        try:
            result.append(int(message_id))
        except (TypeError, ValueError):
            continue

    return result


def save_chat_messages(chat_id: int, message_ids: list[int]) -> None:
    data = read_json(MESSAGES_FILE, {})

    if not isinstance(data, dict):
        data = {}

    # Удаляем дубликаты, сохраняя порядок.
    unique_ids = list(dict.fromkeys(message_ids))

    data[str(chat_id)] = unique_ids

    write_json(MESSAGES_FILE, data)


def add_tracked_message(chat_id: int, message_id: int) -> None:
    messages = get_chat_messages(chat_id)

    if message_id not in messages:
        messages.append(message_id)

    save_chat_messages(chat_id, messages)


async def track_user_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:


    message = update.effective_message
    chat = update.effective_chat

    if message is None or chat is None:
        return

    add_tracked_message(chat.id, message.message_id)


# ============================================================
# CHAT CLEANUP
# ============================================================

async def clean_previous_messages(
    chat_id: int,
    keep_message_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:


    old_message_ids = get_chat_messages(chat_id)


    save_chat_messages(chat_id, [keep_message_id])

    for message_id in old_message_ids:
        if message_id == keep_message_id:
            continue

        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=message_id,
            )

        except TelegramError as error:
            logger.debug(
                "Не удалось удалить сообщение %s в чате %s: %s",
                message_id,
                chat_id,
                error,
            )



async def send_tracked_message(
    chat_id: int,
    text: str,
    context: ContextTypes.DEFAULT_TYPE,
    with_keyboard: bool = True,
    reply_markup=None,
):
    kwargs = {
        "chat_id": chat_id,
        "text": text,
    }

    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    elif with_keyboard:
        kwargs["reply_markup"] = main_keyboard()

    sent_message = await context.bot.send_message(**kwargs)

    add_tracked_message(chat_id, sent_message.message_id)

    return sent_message



# ============================================================
# MENU TEXT
# ============================================================


def get_strategies_text() -> str:
    try:
        from services.strategy_manager import list_strategies

        strategies = list_strategies()

        if not strategies:
            return (
                "🧩 Стратегии MASB\n\n"
                "Стратегии пока не зарегистрированы."
            )

        lines = ["🧩 Стратегии MASB", ""]

        for name, info in strategies.items():
            enabled = bool(info.get("enabled", False))
            status = "🟢 Включена" if enabled else "⚪ Выключена"
            signals = int(info.get("signals", 0))

            lines.extend([
                f"📌 {name}",
                f"Статус: {status}",
                f"Сигналов: {signals}",
            ])

            error = info.get("last_error")
            if error:
                lines.append(f"⚠️ Последняя ошибка: {str(error)[:300]}")

            lines.append("")

        return "\n".join(lines)

    except Exception:
        logger.exception("Не удалось получить список стратегий")
        return (
            "🧩 Стратегии MASB\n\n"
            "Не удалось загрузить список стратегий. "
            "Проверь логи приложения."
        )


def get_section_text(
    section: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> str:

    if section == BTN_STATUS:
        return context.application.bot_data.get(
            "masb_status_text",
            (
                "📊 Состояние MASB\n\n"
                "Бот запущен.\n"
                "Подробная статистика появится после "
                "подключения торгового модуля."
            ),
        )

    if section == BTN_BALANCE:
        return context.application.bot_data.get(
            "masb_balance_text",
            (
                "💰 Баланс MASB\n\n"
                "Начальный виртуальный баланс: 100 USDT\n"
                "Текущий баланс пока не подключён."
            ),
        )

    if section == BTN_STRATEGIES:
        return get_strategies_text()

    if section == BTN_HELP:
        return (
            "❔ Помощь\n\n"
            "📊 Состояние — состояние бота\n"
            "💰 Баланс — информация о балансе\n"
            "🧩 Стратегии — список стратегий\n"
            "❔ Помощь — эта инструкция\n\n"
            "Выбирай раздел кнопками меню."
        )

    return "Главное меню MASB"


# ============================================================
# SHOW SECTION
# ============================================================

async def show_section(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    section: str,
) -> None:

    message = update.effective_message
    chat = update.effective_chat

    if message is None or chat is None:
        return

    if not is_owner(update):
        await clean_previous_messages(
            chat.id,
            message.message_id,
            context,
        )

        await send_tracked_message(
            chat.id,
            "⛔ Доступ запрещён. Этот бот принадлежит другому пользователю.",
            context,
            with_keyboard=False,
        )
        return


    await clean_previous_messages(
        chat.id,
        message.message_id,
        context,
    )

    if section == BTN_STRATEGIES:
        from telegram_bot.strategy_handlers import show_strategies_menu
        await show_strategies_menu(update, context)
        return

    if section == BTN_BALANCE:
        from telegram_bot.strategy_handlers import show_balance_menu
        await show_balance_menu(chat.id, context)
        return

    text = get_section_text(section, context)
    await send_tracked_message(
        chat.id,
        text,
        context,
        with_keyboard=True,
    )


# ============================================================
# /START
# ============================================================

async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if message is None or user is None or chat is None:
        return

    owner_id = get_owner_id()

    if owner_id is None:
        register_owner(user.id)
        logger.info("Зарегистрирован владелец Telegram ID: %s", user.id)

    await show_section(
        update,
        context,
        BTN_HOME,
    )


# ============================================================
# BUTTON HANDLER
# ============================================================

async def menu_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    message = update.effective_message

    if message is None or not message.text:
        return

    text = message.text.strip()

    if text not in MAIN_MENU_BUTTONS:
        return

    await show_section(
        update,
        context,
        text,
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    logger.error(
        "Ошибка Telegram-обработчика",
        exc_info=context.error,
    )


# ============================================================
# REGISTER HANDLERS
# ============================================================

def register_handlers(application: Application) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    application.add_handler(
        MessageHandler(
            filters.ALL,
            track_user_message,
        ),
        group=-1,
    )

    application.add_handler(
        CommandHandler("start", cmd_start),
        group=0,
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            menu_handler,
        ),
        group=0,
    )

    application.add_error_handler(error_handler)

    logger.info("Обработчики MASB зарегистрированы.")