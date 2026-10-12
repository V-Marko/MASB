from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from telegram import Update
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from telegram.request import HTTPXRequest

from config import LOGS_DIR, PROJECT_ROOT
from core.json_storage import JsonStorage
from telegram_bot.handlers import (
    button_callback,
    cmd_balance,
    cmd_help,
    cmd_start,
    cmd_status,
    cmd_strategies,
    load_owner_id,
)


def configure_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if root.handlers:
        return

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOGS_DIR / "masb.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root.addHandler(console)
    root.addHandler(file_handler)


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Записывает ошибки обработчиков, не выводя токен."""
    error = context.error

    if isinstance(error, NetworkError):
        logging.error(
            "Telegram network error while processing an update. "
            "Check internet access and Telegram API connectivity."
        )
        return

    logging.error(
        "Unhandled error while processing Telegram update.",
        exc_info=(type(error), error, error.__traceback__) if error else None,
    )


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    configure_logging()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    if not token or token == "replace_with_your_bot_token":
        raise SystemExit(
            "Не задан TELEGRAM_BOT_TOKEN. "
            "Укажи действующий токен в .env. "
            "Не публикуй его в GitHub и не отправляй в чат."
        )

    storage = JsonStorage()
    config = storage.load("config")

    # Если файл владельца повреждён, запуск останавливается ради безопасности.
    owner_id = load_owner_id()

    logging.info(
        "MASB started. Virtual balance: %s USDT; leverage: %sx. "
        "Database and live orders are not used.",
        config["virtual_balance"],
        config["leverage"],
    )

    if owner_id is None:
        logging.warning(
            "Owner not registered. The first private /start claims ownership."
        )
    else:
        logging.info("Saved Telegram owner loaded.")

    # Отдельные запросы с увеличенными тайм-аутами.
    normal_request = HTTPXRequest(
        connect_timeout=20,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=20,
        connection_pool_size=16,
    )
    polling_request = HTTPXRequest(
        connect_timeout=20,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=20,
        connection_pool_size=4,
    )

    application = (
        Application.builder()
        .token(token)
        .request(normal_request)
        .get_updates_request(polling_request)
        .build()
    )

    application.bot_data["storage"] = storage
    application.bot_data["owner_user_id"] = owner_id

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("balance", cmd_balance))
    application.add_handler(CommandHandler("strategies", cmd_strategies))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)

    logging.info("Starting Telegram polling. Press Ctrl+C to stop.")

    application.run_polling(
        allowed_updates=["message", "callback_query"],
        bootstrap_retries=5,
    )


if __name__ == "__main__":
    main()