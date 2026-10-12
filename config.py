"""Базовая конфигурация MASB. Торговые правила здесь не задаются."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
TEMPLATES_DIR = PROJECT_ROOT / "data_templates"
LOGS_DIR = PROJECT_ROOT / "logs"

DEFAULT_VIRTUAL_BALANCE = "100"
DEFAULT_LEVERAGE = 10
DEFAULT_CAPITAL_MODE = "per_strategy"  # per_strategy | shared

JSON_FILES = {
    "config": DATA_DIR / "config.json",
    "realtime_state": DATA_DIR / "realtime_state.json",
    "open_orders": DATA_DIR / "open_orders.json",
    "trade_history": DATA_DIR / "trade_history.json",
    "strategy_stats": DATA_DIR / "strategy_stats.json",
    "backtest_results": DATA_DIR / "backtest_results.json",
}

INITIAL_JSON_DATA = {
    "config": {
        "virtual_balance": DEFAULT_VIRTUAL_BALANCE,
        "leverage": DEFAULT_LEVERAGE,
        "capital_mode": DEFAULT_CAPITAL_MODE,
        "active_strategies": [],
        "quote_currency": "USDT",
        "note": "Безопасные стартовые настройки. Торговые стратегии пока не реализованы.",
    },
    "realtime_state": {
        "schema_version": 1,
        "running": False,
        "last_processed_candles": {},
        "last_signals": {},
        "balances": {},
        "positions": [],
    },
    "open_orders": {"schema_version": 1, "orders": []},
    "trade_history": {"schema_version": 1, "trades": []},
    "strategy_stats": {"schema_version": 1, "strategies": {}},
    "backtest_results": {"schema_version": 1, "runs": []},
}

IMPORTANT_JSON_KEYS = {
    "realtime_state", "open_orders", "trade_history", "strategy_stats", "backtest_results"
}
