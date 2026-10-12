"""Форматирование сообщений MASB."""

from typing import Any


def status_message(
    config: dict[str, Any],
    realtime_state: dict[str, Any],
) -> str:
    running = bool(realtime_state.get("running", False))
    running_label = "включена" if running else "не запущена"

    return (
        "🤖 *MASB — состояние*\n\n"
        "Telegram: подключён\n"
        "Режим: виртуальная торговля / тестовая основа\n"
        f"Обработка realtime: {running_label}\n"
        f"Виртуальный стартовый баланс: "
        f"{config.get('virtual_balance', '100')} USDT\n"
        f"Плечо по умолчанию: {config.get('leverage', 10)}x\n\n"
        "Рыночные данные, сигналы и сделки пока не подключены."
    )


def balance_message(config: dict[str, Any]) -> str:
    mode = config.get("capital_mode", "per_strategy")

    mode_labels = {
        "per_strategy": "отдельный баланс на стратегию",
        "shared": "общий баланс стратегий",
    }
    mode_label = mode_labels.get(mode, str(mode))

    return (
        "💰 *Виртуальный капитал*\n\n"
        f"Стартовый баланс: {config.get('virtual_balance', '100')} USDT\n"
        f"Плечо по умолчанию: {config.get('leverage', 10)}x\n"
        f"Режим капитала: {mode_label}\n\n"
        "Это настроечный стартовый баланс, а не расчёт текущего equity. "
        "Открытые позиции и PnL пока не рассчитываются."
    )


def strategies_message(config: dict[str, Any]) -> str:
    active = config.get("active_strategies", [])
    active_text = ", ".join(map(str, active)) if active else "нет"

    return (
        "🧩 *Стратегии MASB*\n\n"
        "В проекте запланированы модули:\n"
        "• Fibo 0.8 + ZigZag 5%\n"
        "• Fibo 0.8 + ZigZag 10%\n"
        "• Fibo 0.8/0.6 + ZigZag 5%\n"
        "• Fibo 0.8/0.6 + ZigZag 10%\n\n"
        f"Включены в конфигурации: {active_text}.\n"
        "Логика этих стратегий ещё не реализована; "
        "бот не открывает сделки."
    )


def help_message() -> str:
    return (
        "❔ *Команды MASB*\n\n"
        "/start — открыть меню\n"
        "/status — состояние приложения\n"
        "/balance — параметры виртуального капитала\n"
        "/strategies — список стратегий\n"
        "/help — список команд\n\n"
        "Используй кнопки меню для навигации."
    )