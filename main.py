
import logging
import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv
from telegram.ext import Application
from telegram.request import HTTPXRequest

from telegram_bot.handlers import register_handlers
from telegram_bot.strategy_handlers import register_strategy_handlers
from telegram_bot.panel_handlers import register_panel_handlers


# ============================================================
# PATHS
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# ENVIRONMENT
# ============================================================
load_dotenv(ENV_FILE)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

if not TELEGRAM_BOT_TOKEN:
    print(
        "ERROR: TELEGRAM_BOT_TOKEN не найден в файле .env",
        file=sys.stderr,
    )
    sys.exit(1)


# ============================================================
# LOGGING FILTER
# ============================================================
class TelegramPollingNetworkErrorFilter(logging.Filter):
    """
    Hide Telegram polling network tracebacks from the console.
    File logging remains unaffected.
    """

    NETWORK_ERRORS = (
        "httpx.ConnectError",
        "httpcore.ConnectError",
        "telegram.error.NetworkError",
        "httpx.ReadError",
        "httpx.WriteError",
        "httpx.RemoteProtocolError",
        "httpcore.ReadError",
        "httpcore.WriteError",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.exc_info:
            return True

        if not record.name.startswith(
            "telegram.ext._utils.networkloop"
        ):
            return True

        try:
            error_text = "".join(
                traceback.format_exception(*record.exc_info)
            )
        except Exception:
            return True

        return not any(
            error_name in error_text
            for error_name in self.NETWORK_ERRORS
        )


class TelegramConsoleErrorFilter(logging.Filter):
    """Suppress known repetitive Telegram errors in console output only."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()

        # A handler trying to send a duplicate message is harmless.
        if (
            record.name == "MASB.telegram.handlers"
            and "Ошибка Telegram-обработчика" in message
            and record.exc_info
        ):
            exc_type, exc_value, _ = record.exc_info
            if exc_type is not None and exc_value is not None:
                if exc_type.__name__ == "RetryAfter":
                    return False

                if (
                    exc_type.__name__ == "BadRequest"
                    and "Message is not modified" in str(exc_value)
                ):
                    return False

        return True



# ============================================================
# LOGGING CONFIGURATION
# ============================================================
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.addFilter(TelegramConsoleErrorFilter())
file_handler = logging.FileHandler(
    LOGS_DIR / "telegram.log",
    encoding="utf-8",
)
file_handler.setLevel(logging.INFO)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        console_handler,
        file_handler,
    ],
    force=True,
)

# Reduce low-level HTTP library messages.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("MASB")


# ============================================================
# TELEGRAM CONNECTION
# ============================================================
def build_application() -> Application:
    main_request = HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=20,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=20,
    )

    polling_request = HTTPXRequest(
        connection_pool_size=4,
        connect_timeout=20,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=20,
    )

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(main_request)
        .get_updates_request(polling_request)
        .build()
    )

    register_handlers(application)
    register_strategy_handlers(application)
    register_panel_handlers(application)

    logger.info(
        "Обработчики Telegram, менеджер стратегий "
        "и живая панель зарегистрированы."
    )

    return application


# ============================================================
# START
# ============================================================
def main() -> None:
    logger.info("Запуск MASB Telegram Bot...")

    application = build_application()

    try:
        application.run_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=False,
            bootstrap_retries=5,
            close_loop=True,
        )

    except KeyboardInterrupt:
        logger.info("Остановка по запросу пользователя.")

    except Exception:
        logger.exception(
            "Критическая ошибка при запуске MASB."
        )
        raise

    finally:
        logger.info("MASB Telegram Bot остановлен.")


if __name__ == "__main__":
    main()

