
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram.ext import Application
from telegram.request import HTTPXRequest

from telegram_bot.handlers import register_handlers
from telegram_bot.strategy_handlers import register_strategy_handlers
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
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOGS_DIR / "telegram.log",
            encoding="utf-8",
        ),
    ],
)

# Снижаем количество технических сообщений библиотек.
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

    logger.info("Обработчики Telegram и менеджер стратегий зарегистрированы.")


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
        logger.exception("Критическая ошибка при запуске MASB.")
        raise

    finally:
        logger.info("MASB Telegram Bot остановлен.")


if __name__ == "__main__":
    main()
