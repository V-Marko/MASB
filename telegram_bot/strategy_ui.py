
import json
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
)

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
OWNER_FILE = DATA_DIR / "telegram_owner.json"
SETTINGS_FILE = DATA_DIR / "strategy_settings.json"

DEFAULT_BALANCE = 100.0

# Список стратегий берётся автоматически из папки strategy/.
from strategy.registry import discover_strategies

STRATEGIES = discover_strategies()[2]


def load_json(path: Path, default=None):
    if default is None:
        default = {}

    try:
        if not path.exists():
            return default.copy()

        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, dict) else default.copy()

    except (OSError, json.JSONDecodeError):
        logger.exception("Ошибка чтения файла %s", path)
        return default.copy()


def save_json(path: Path, data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    temp_path.replace(path)


def get_settings():
    return load_json(SETTINGS_FILE, {})


def is_enabled(name: str) -> bool:
    settings = get_settings()
    return bool(settings.get(name, {}).get("enabled", True))


def set_enabled(name: str, enabled: bool):
    settings = get_settings()

    if name not in settings:
        settings[name] = {}

    settings[name]["enabled"] = enabled
    save_json(SETTINGS_FILE, settings)


def notifications_enabled(name: str) -> bool:
    settings = get_settings()
    return bool(settings.get(name, {}).get("notifications", True))


def toggle_notifications(name: str):
    settings = get_settings()

    if name not in settings:
        settings[name] = {}

    current = bool(settings[name].get("notifications", True))
    settings[name]["notifications"] = not current

    save_json(SETTINGS_FILE, settings)


def get_owner_id():
    owner_data = load_json(OWNER_FILE, {})
    owner_id = owner_data.get("owner_id")

    try:
        return int(owner_id) if owner_id is not None else None
    except (ValueError, TypeError):
        return None


def is_authorized(update: Update) -> bool:
    owner_id = get_owner_id()

    user = update.effective_user

    return (
        owner_id is not None
        and user is not None
        and user.id == owner_id
    )


def get_strategy_info(name: str) -> dict:
    try:
        from services.strategy_manager import list_strategies

        strategies = list_strategies()

        if isinstance(strategies, dict):
            return strategies.get(name, {})

    except (ImportError, AttributeError):
        logger.warning(
            "Не удалось получить данные из strategy_manager"
        )
    except Exception:
        logger.exception("Ошибка получения статистики стратегии")

    return {}


def strategies_keyboard():
    rows = []

    for name in STRATEGIES:
        status = "🟢" if is_enabled(name) else "⚪"

        rows.append([
            InlineKeyboardButton(
                f"{status} {name}",
                callback_data=f"strategy:open:{name}",
            )
        ])

    return InlineKeyboardMarkup(rows)


def strategy_keyboard(name: str):
    enabled = is_enabled(name)
    notifications = notifications_enabled(name)

    toggle_text = (
        "⏹ Выключить стратегию"
        if enabled
        else "🟢 Включить стратегию"
    )

    notification_text = (
        "🔔 Уведомления: ВКЛ"
        if notifications
        else "🔕 Уведомления: ВЫКЛ"
    )

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                toggle_text,
                callback_data=f"strategy:toggle:{name}",
            )
        ],
        [
            InlineKeyboardButton(
                notification_text,
                callback_data=f"strategy:notify:{name}",
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Баланс",
                callback_data=f"strategy:balance:{name}",
            ),
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data=f"strategy:stats:{name}",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="strategy:list",
            )
        ],
    ])


def list_text() -> str:
    lines = [
        "🧩 Стратегии MASB",
        "",
        "Выбери стратегию для управления:",
        "",
    ]

    for name in STRATEGIES:
        status = "🟢 Включена" if is_enabled(name) else "⚪ Выключена"
        info = get_strategy_info(name)
        signals = info.get("signals", 0)

        lines.extend([
            f"📌 {name}",
            f"Статус: {status}",
            f"Сигналов: {signals}",
            "",
        ])

    return "\n".join(lines).strip()


def detail_text(name: str) -> str:
    info = get_strategy_info(name)
    enabled = is_enabled(name)
    notifications = notifications_enabled(name)

    status = "🟢 Включена" if enabled else "⚪ Выключена"
    notification_status = "ВКЛ" if notifications else "ВЫКЛ"

    return (
        f"📌 Стратегия: {name}\n\n"
        f"Описание: {STRATEGIES.get(name, name)}\n"
        f"Статус: {status}\n"
        f"Уведомления: {notification_status}\n"
        f"Сигналов: {info.get('signals', 0)}\n"
    )


def balance_text(name: str) -> str:
    settings = get_settings()
    strategy_settings = settings.get(name, {})

    balance = strategy_settings.get(
        "virtual_balance",
        DEFAULT_BALANCE,
    )

    try:
        balance = float(balance)
    except (ValueError, TypeError):
        balance = DEFAULT_BALANCE

    return (
        f"💰 Баланс стратегии {name}\n\n"
        f"Начальный виртуальный баланс: "
        f"{DEFAULT_BALANCE:.2f} USDT\n"
        f"Сохранённый виртуальный баланс: "
        f"{balance:.2f} USDT\n\n"
        "⚠️ Баланс не является реальным балансом биржи. "
        "Пока результаты закрытых сделок автоматически "
        "не учитываются."
    )


def stats_text(name: str) -> str:
    info = get_strategy_info(name)

    signals = info.get("signals", 0)
    last_error = info.get("last_error") or "Нет данных об ошибках"
    status = "Включена" if is_enabled(name) else "Выключена"

    return (
        f"📊 Статистика {name}\n\n"
        f"Статус: {status}\n"
        f"Количество сигналов: {signals}\n\n"
        "Закрытые сделки: нет данных\n"
        "Винрейт: нет данных\n"
        "Прибыль / убыток: нет данных\n\n"
        f"Последняя ошибка: {last_error}"
    )


async def show_strategies(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = list_text()
    markup = strategies_keyboard()

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=text,
            reply_markup=markup,
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            text=text,
            reply_markup=markup,
        )


async def strategy_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not is_authorized(update):
        await query.answer(
            "Нет доступа к управлению ботом.",
            show_alert=True,
        )
        return

    parts = query.data.split(":")

    if len(parts) < 2:
        return

    action = parts[1]

    if action == "list":
        await show_strategies(update, context)
        return

    if len(parts) != 3:
        return

    name = parts[2]

    if name not in STRATEGIES:
        await query.edit_message_text("Стратегия не найдена.")
        return

    if action == "open":
        await query.edit_message_text(
            detail_text(name),
            reply_markup=strategy_keyboard(name),
        )
        return

    if action == "toggle":
        set_enabled(name, not is_enabled(name))

        # Синхронизируем статус с основным менеджером,
        # если он предоставляет соответствующую функцию.
        try:
            from services.strategy_manager import set_strategy_enabled

            set_strategy_enabled(name, is_enabled(name))
        except (ImportError, AttributeError):
            logger.warning(
                "Не удалось синхронизировать статус "
                "с strategy_manager для %s",
                name,
            )
        except Exception:
            logger.exception("Ошибка синхронизации статуса стратегии")

        await query.edit_message_text(
            detail_text(name),
            reply_markup=strategy_keyboard(name),
        )
        return

    if action == "notify":
        toggle_notifications(name)

        await query.edit_message_text(
            detail_text(name),
            reply_markup=strategy_keyboard(name),
        )
        return

    if action == "balance":
        await query.edit_message_text(
            balance_text(name),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Назад к стратегии",
                        callback_data=f"strategy:open:{name}",
                    )
                ]
            ]),
        )
        return

    if action == "stats":
        await query.edit_message_text(
            stats_text(name),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Назад к стратегии",
                        callback_data=f"strategy:open:{name}",
                    )
                ]
            ]),
        )


async def strategies_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_authorized(update):
        await update.effective_message.reply_text("Нет доступа.")
        return

    await show_strategies(update, context)


async def open_strategies_from_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_authorized(update):
        await update.effective_message.reply_text("Нет доступа.")
        return

    await show_strategies(update, context)


def register_strategy_ui(application: Application):
    application.add_handler(
        CallbackQueryHandler(
            strategy_callback,
            pattern=r"^strategy:",
        )
    )
