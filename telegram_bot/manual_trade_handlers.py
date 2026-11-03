from __future__ import annotations


import asyncio
import hashlib
import hmac
import logging
import os
import time
import urllib.parse
import urllib.request
import json
from decimal import Decimal, ROUND_DOWN
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from telegram_bot.handlers import is_owner

logger = logging.getLogger("MASB.manual_testnet")
BASE_URL = "https://testnet.binancefuture.com"  # intentionally TESTNET only
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT",
           "AVAXUSDT", "LTCUSDT", "TRXUSDT", "DOTUSDT", "ATOMUSDT", "NEARUSDT", "ETCUSDT", "APTUSDT"]
FIELDS = ["entry", "tp", "sl", "leverage"]
PROMPTS = {
    "entry": "Отправь LIMIT Entry отдельным сообщением (число больше 0):",
    "tp": "Отправь Take Profit (TP) отдельным сообщением:",
    "sl": "Отправь Stop Loss (SL) отдельным сообщением:",
    "leverage": "Отправь плечо целым числом (например, 2 или 3):",
}

class TestnetError(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    key = os.getenv("BINANCE_TESTNET_API_KEY", "").strip()
    secret = os.getenv("BINANCE_TESTNET_API_SECRET", "").strip()
    if not key or not secret:
        raise TestnetError("Не заданы BINANCE_TESTNET_API_KEY и BINANCE_TESTNET_API_SECRET в .env")
    return key, secret


def _request(method: str, path: str, params: dict[str, Any] | None = None, signed: bool = False) -> Any:
    params = dict(params or {})
    key = ""
    secret = ""
    if signed:
        key, secret = _credentials()
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 10000
        query = urllib.parse.urlencode(params)
        params["signature"] = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    data = urllib.parse.urlencode(params).encode() if method in {"POST", "PUT", "DELETE"} else None
    url = BASE_URL + path
    if method == "GET" and params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"X-MBX-APIKEY": key} if signed else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = response.read().decode("utf-8")
            result = json.loads(payload) if payload else {}
    except Exception as exc:
        detail = ""
        if hasattr(exc, "read"):
            try: detail = exc.read().decode("utf-8", "replace")
            except Exception: pass
        raise TestnetError(f"Binance Futures TESTNET: {exc}. {detail}".strip()) from exc
    if isinstance(result, dict) and result.get("code", 0) not in (0, None):
        raise TestnetError(f"Binance TESTNET {result.get('code')}: {result.get('msg')}")
    return result


def _rules(symbol: str) -> dict[str, Decimal]:
    info = _request("GET", "/fapi/v1/exchangeInfo")
    for item in info.get("symbols", []):
        if item.get("symbol") == symbol:
            filters = {x["filterType"]: x for x in item.get("filters", [])}
            return {
                "tick": Decimal(filters["PRICE_FILTER"]["tickSize"]),
                "step": Decimal(filters["LOT_SIZE"]["stepSize"]),
                "min_qty": Decimal(filters["LOT_SIZE"]["minQty"]),
            }
    raise TestnetError(f"Монета {symbol} недоступна на Futures TESTNET")


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _fmt(value: Decimal) -> str:
    return format(value, "f")


def _place_testnet_order(trade: dict[str, Any]) -> dict[str, Any]:
    # Hardcoded TESTNET host above is the only destination used by this module.
    symbol = trade["symbol"]
    direction = trade["direction"]
    entry, tp, sl = (Decimal(str(trade[k])) for k in ("entry", "tp", "sl"))
    leverage = int(trade["leverage"])
    margin = Decimal(os.getenv("MASB_TESTNET_MARGIN_USDT", "5"))
    if margin <= 0 or leverage < 1 or leverage > 20:
        raise TestnetError("Проверь MASB_TESTNET_MARGIN_USDT и плечо (1–20 для тестового модуля)")
    if direction == "LONG" and not sl < entry < tp:
        raise TestnetError("Для LONG требуется SL < Entry < TP")
    if direction == "SHORT" and not tp < entry < sl:
        raise TestnetError("Для SHORT требуется TP < Entry < SL")

    rules = _rules(symbol)
    entry = _floor(entry, rules["tick"])
    tp = _floor(tp, rules["tick"])
    sl = _floor(sl, rules["tick"])
    qty = _floor((margin * leverage) / entry, rules["step"])
    if qty < rules["min_qty"]:
        raise TestnetError(f"Расчётное количество {qty} меньше минимального {rules['min_qty']}")

    _request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, signed=True)
    side = "BUY" if direction == "LONG" else "SELL"
    entry_order = _request("POST", "/fapi/v1/order", {
        "symbol": symbol, "side": side, "type": "LIMIT", "timeInForce": "GTC",
        "quantity": _fmt(qty), "price": _fmt(entry), "newOrderRespType": "RESULT",
    }, signed=True)

    # Protective conditional close orders are sent to TESTNET only.
    close_side = "SELL" if direction == "LONG" else "BUY"
    try:
        sl_order = _request("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL", "symbol": symbol, "side": close_side,
            "type": "STOP_MARKET", "triggerPrice": _fmt(sl), "closePosition": "true",
            "workingType": "MARK_PRICE", "priceProtect": "TRUE",
        }, signed=True)
        tp_order = _request("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL", "symbol": symbol, "side": close_side,
            "type": "TAKE_PROFIT_MARKET", "triggerPrice": _fmt(tp), "closePosition": "true",
            "workingType": "MARK_PRICE", "priceProtect": "TRUE",
        }, signed=True)
    except Exception:
        # Do not silently leave an entry live without protection: cancel it.
        try:
            _request("DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": entry_order["orderId"]}, signed=True)
        except Exception:
            logger.exception("Could not cancel TESTNET entry after protection setup failure")
        raise
    return {"entry": entry_order, "sl": sl_order, "tp": tp_order, "qty": qty,
            "entry_price": entry, "tp_price": tp, "sl_price": sl, "margin": margin, "leverage": leverage}


async def show_trade_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    rows = [[InlineKeyboardButton(s, callback_data=f"mtest:symbol:{s}")] for s in SYMBOLS]
    rows.append([InlineKeyboardButton("✖ Cancel", callback_data="mtest:cancel")])
    await update.effective_message.reply_text(
        "🧪 MANUAL FUTURES — TESTNET ONLY\nВыбери тестовую монету. Реальный Binance аккаунт этим модулем не используется.",
        reply_markup=InlineKeyboardMarkup(rows))


async def _ask_field(update: Update, context: ContextTypes.DEFAULT_TYPE, field: str) -> None:
    context.user_data["masb_testnet_step"] = field
    await update.effective_message.reply_text(PROMPTS[field])


async def _callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_owner(update):
        await query.edit_message_text("⛔ Доступ запрещён.")
        return
    data = query.data or ""
    if data == "mtest:cancel":
        context.user_data.pop("masb_testnet_trade", None)
        context.user_data.pop("masb_testnet_step", None)
        await query.edit_message_text("Ввод тестовой сделки отменён.")
        return
    if data.startswith("mtest:symbol:"):
        symbol = data.split(":", 2)[2]
        context.user_data["masb_testnet_trade"] = {"symbol": symbol}
        await query.edit_message_text(f"{symbol}: выбери направление", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 LONG", callback_data="mtest:direction:LONG"), InlineKeyboardButton("🔴 SHORT", callback_data="mtest:direction:SHORT")],
            [InlineKeyboardButton("Cancel", callback_data="mtest:cancel")]]))
        return
    if data.startswith("mtest:direction:"):
        trade = context.user_data.get("masb_testnet_trade")
        if not trade:
            await query.edit_message_text("Сессия истекла. Нажми Trade TESTNET ещё раз.")
            return
        trade["direction"] = data.rsplit(":", 1)[1]
        await query.edit_message_text(f"{trade['symbol']} · {trade['direction']} · LIMIT · TESTNET")
        await _ask_field(update, context, "entry")
        return
    if data == "mtest:confirm":
        trade = context.user_data.get("masb_testnet_trade")
        if not trade:
            await query.edit_message_text("Сессия истекла. Начни заново.")
            return
        await query.edit_message_text("Отправляю LIMIT и защитные ордера в Binance Futures TESTNET…")
        try:
            result = await asyncio.to_thread(_place_testnet_order, trade)
        except Exception as exc:
            logger.exception("TESTNET manual order failed")
            await query.message.reply_text(f"❌ TESTNET: {exc}")
        else:
            await query.message.reply_text(
                f"✅ TESTNET ордера приняты\n{trade['symbol']} · {trade['direction']}\n"
                f"LIMIT Entry: {result['entry_price']}\nTP: {result['tp_price']}\nSL: {result['sl_price']}\n"
                f"Leverage: {result['leverage']}x\nТестовая маржа: {result['margin']} USDT\n"
                f"Qty: {result['qty']}\nEntry order ID: {result['entry'].get('orderId')}\n"
                f"SL algo ID: {result['sl'].get('algoId')}\nTP algo ID: {result['tp'].get('algoId')}\n"
                "Это TESTNET, реальные средства не затрагиваются.")
        finally:
            context.user_data.pop("masb_testnet_trade", None)
            context.user_data.pop("masb_testnet_step", None)


async def _text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    step = context.user_data.get("masb_testnet_step")
    trade = context.user_data.get("masb_testnet_trade")
    if not step or not trade or update.effective_message is None:
        return
    if not is_owner(update):
        return
    raw = (update.effective_message.text or "").strip().replace(",", ".")
    try:
        value = Decimal(raw)
        if not value.is_finite() or value <= 0:
            raise ValueError
        if step == "leverage" and (value != value.to_integral_value() or value > 20):
            raise ValueError
    except Exception:
        await update.effective_message.reply_text("Некорректное значение. Введи положительное число; плечо — целое от 1 до 20.")
        return
    trade[step] = int(value) if step == "leverage" else str(value)
    idx = FIELDS.index(step)
    if idx + 1 < len(FIELDS):
        await _ask_field(update, context, FIELDS[idx + 1])
        return
    try:
        entry, tp, sl = (Decimal(trade[k]) for k in ("entry", "tp", "sl"))
        if trade["direction"] == "LONG" and not sl < entry < tp:
            raise ValueError("Для LONG должно быть SL < Entry < TP")
        if trade["direction"] == "SHORT" and not tp < entry < sl:
            raise ValueError("Для SHORT должно быть TP < Entry < SL")
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Проверка цен: {exc}. Ввод отменён; начни заново.")
        context.user_data.pop("masb_testnet_trade", None)
        context.user_data.pop("masb_testnet_step", None)
        return
    context.user_data.pop("masb_testnet_step", None)
    margin = os.getenv("MASB_TESTNET_MARGIN_USDT", "5")
    summary = (f"ПРОВЕРЬ ПЕРЕД ОТПРАВКОЙ — TESTNET ONLY\n\n{trade['symbol']} · {trade['direction']}\n"
               f"Тип входа: LIMIT\nEntry: {trade['entry']}\nTP: {trade['tp']}\nSL: {trade['sl']}\n"
               f"Leverage: {trade['leverage']}x\nТестовая маржа из .env: {margin} USDT\n\n"
               "Подтвердить отправку тестовых ордеров?")
    await update.effective_message.reply_text(summary, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ CONFIRM TESTNET", callback_data="mtest:confirm")],
        [InlineKeyboardButton("✖ Cancel", callback_data="mtest:cancel")]]))


def register_manual_testnet_handlers(application: Application) -> None:
    application.add_handler(CallbackQueryHandler(_callback, pattern=r"^mtest:"), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _text), group=1)
    logger.info("Manual Futures TESTNET handlers registered")
