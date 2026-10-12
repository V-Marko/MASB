import logging
from logging.handlers import RotatingFileHandler

from config import LOGS_DIR
from core.json_storage import JsonStorage


def configure_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        return
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        LOGS_DIR / "masb.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(console)
    root.addHandler(file_handler)


def main() -> None:
    configure_logging()
    storage = JsonStorage()
    config = storage.load("config")
    logging.info("MASB foundation initialized; database engines are not used.")
    logging.info("Virtual balance: %s USDT; leverage: %sx", config["virtual_balance"], config["leverage"])
    logging.info("No trading strategies are running in this foundation build.")


if __name__ == "__main__":
    main()
