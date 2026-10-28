from telegram import ReplyKeyboardMarkup, KeyboardButton


BTN_STATUS = "📊 Состояние"
BTN_BALANCE = "💰 Баланс"
BTN_ACTIVE_TRADES = "📈 Active trades"
BTN_STRATEGIES = "🧩 Стратегии"
BTN_SETTINGS = "⚙️ Настройки"
BTN_HELP = "❔ Помощь"
BTN_HOME = "⬅️ Главное меню"

MAIN_MENU_BUTTONS = {
    BTN_STATUS,
    BTN_BALANCE,
    BTN_ACTIVE_TRADES,
    BTN_STRATEGIES,
    BTN_SETTINGS,
    BTN_HELP,
    BTN_HOME,
}


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(BTN_STATUS),
                KeyboardButton(BTN_BALANCE),
            ],
            [
                KeyboardButton(BTN_ACTIVE_TRADES),
                KeyboardButton(BTN_STRATEGIES),
            ],
            [
                KeyboardButton(BTN_SETTINGS),
            ],
            [
                KeyboardButton(BTN_HELP),
            ],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        selective=False,
        input_field_placeholder="Состояние • Баланс • Active trades • Стратегии • Настройки",
    )
