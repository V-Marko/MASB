"""Кнопки главного меню Telegram."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Состояние", callback_data="status"),
                InlineKeyboardButton("💰 Баланс", callback_data="balance"),
            ],
            [
                InlineKeyboardButton("🧩 Стратегии", callback_data="strategies"),
                InlineKeyboardButton("❔ Помощь", callback_data="help"),
            ],
        ]
    )