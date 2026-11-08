from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from config import DATA_DIR, PROJECT_ROOT
from services.strategy_manager import strategy_real_enabled

logger = logging.getLogger("MASB.BinanceFutures")
load_dotenv(PROJECT_ROOT / ".env")

BASE_URL = "https://fapi.binance.com"
STATE_FILE = DATA_DIR / "real_orders.json"
STRATEGY_ALLOWLIST: set[str] = set()  # Legacy constant; per-strategy state is authoritative.
MAX_OPEN_POSITIONS = 7
MAX_LEVERAGE = 5
DEFAULT_STRATEGY_MARGIN_USDT = 10.0  # fixed margin per strategy REAL trade
RISK_PERCENT = 1.0
DEFAULT_REAL_CAPITAL_USDT = 100.0
COMMISSION_PER_SIDE = 0.00055
RECV_WINDOW = 5000
_PREFIX = "MASB"
_LOCK = threading.RLock()


class BinanceFuturesError(RuntimeError):
    pass



def _strategy_margin_usdt() -> float:
    """Fixed margin (USDT) for strategy REAL trades. Override via MASB_STRATEGY_MARGIN_USDT."""
    try:
        value = float(os.getenv("MASB_STRATEGY_MARGIN_USDT", str(DEFAULT_STRATEGY_MARGIN_USDT)))
    except (TypeError, ValueError) as exc:
        raise BinanceFuturesError("MASB_STRATEGY_MARGIN_USDT должен быть числом") from exc
    if not math.isfinite(value) or value <= 0:
        raise BinanceFuturesError("MASB_STRATEGY_MARGIN_USDT должен быть больше нуля")
    return value


def _num(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def real_trading_enabled() -> bool:
    return os.getenv("MASB_REAL_TRADING_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def real_status_summary() -> dict[str, Any]:
    """Данные для экрана «REAL статус» (без обращений к Binance)."""
    try:
        margin = _strategy_margin_usdt()
    except BinanceFuturesError:
        margin = None
    try:
        orders = _read_state().get("orders", [])
    except BinanceFuturesError:
        orders = []
    active = [o for o in orders if o.get("status") in {"PENDING", "OPEN"}]
    return {
        "global_enabled": real_trading_enabled(),
        "has_keys": bool(os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_API_SECRET")),
        "margin_usdt": margin,
        "leverage": MAX_LEVERAGE,
        "max_positions": MAX_OPEN_POSITIONS,
        "active_orders": active,
    }


def _configured_capital() -> float:
    try:
        value = float(os.getenv("MASB_REAL_CAPITAL_USDT", str(DEFAULT_REAL_CAPITAL_USDT)))
    except (TypeError, ValueError) as exc:
        raise BinanceFuturesError("MASB_REAL_CAPITAL_USDT должен быть числом") from exc
    if not math.isfinite(value) or value <= 0:
        raise BinanceFuturesError("MASB_REAL_CAPITAL_USDT должен быть больше нуля")
    return value


def _credentials() -> tuple[str, str]:
    key = os.getenv("BINANCE_API_KEY", "").strip()
    secret = os.getenv("BINANCE_API_SECRET", "").strip()
    if not key or not secret:
        raise BinanceFuturesError("Не заданы BINANCE_API_KEY / BINANCE_API_SECRET в .env")
    return key, secret


def _request(method: str, path: str, params: dict[str, Any] | None = None, *, signed: bool = False) -> Any:
    params = {k: v for k, v in (params or {}).items() if v is not None}
    headers = {"User-Agent": "MASB/2.0"}
    if signed:
        api_key, secret = _credentials()
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = RECV_WINDOW
        query = urllib.parse.urlencode(params)
        signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        query = f"{query}&signature={signature}"
        headers["X-MBX-APIKEY"] = api_key
    else:
        query = urllib.parse.urlencode(params)

    url = f"{BASE_URL}{path}"
    data = None
    if method.upper() == "GET" or method.upper() == "DELETE":
        if query:
            url = f"{url}?{query}"
    else:
        data = query.encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise BinanceFuturesError(f"Binance HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise BinanceFuturesError(f"Ошибка запроса Binance {path}: {exc}") from exc

    try:
        result = json.loads(payload) if payload else {}
    except json.JSONDecodeError as exc:
        raise BinanceFuturesError(f"Binance вернул некорректный JSON для {path}") from exc
    if isinstance(result, dict) and "code" in result and int(result.get("code", 0)) < 0:
        raise BinanceFuturesError(f"Binance {result.get('code')}: {result.get('msg', 'API error')}")
    return result


def _read_state() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        return {"schema_version": 1, "orders": []}
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("orders"), list):
            raise ValueError("Неверная структура real_orders.json")
        return payload
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise BinanceFuturesError(f"Не удалось прочитать {STATE_FILE}: {exc}") from exc


def _write_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _round_step(value: float, step: str, rounding: str = ROUND_DOWN) -> str:
    number = _decimal(value)
    quantum = _decimal(step)
    if quantum <= 0:
        raise BinanceFuturesError("Binance вернул некорректный tickSize/stepSize")
    rounded = (number / quantum).to_integral_value(rounding=rounding) * quantum
    return format(rounded, "f")


def _symbol_rules(symbol: str) -> dict[str, Any]:
    info = _request("GET", "/fapi/v1/exchangeInfo")
    for item in info.get("symbols", []):
        if item.get("symbol") != symbol:
            continue
        filters = {f.get("filterType"): f for f in item.get("filters", [])}
        lot = filters.get("LOT_SIZE") or filters.get("MARKET_LOT_SIZE")
        price = filters.get("PRICE_FILTER")
        if not lot or not price:
            break
        return {
            "status": item.get("status"),
            "quoteAsset": item.get("quoteAsset"),
            "stepSize": lot.get("stepSize"),
            "minQty": lot.get("minQty"),
            "maxQty": lot.get("maxQty"),
            "tickSize": price.get("tickSize"),
            "minNotional": (filters.get("MIN_NOTIONAL") or {}).get("notional", "5"),
        }
    raise BinanceFuturesError(f"Не удалось получить торговые фильтры для {symbol}")


def _assert_one_way_mode() -> None:
    mode = _request("GET", "/fapi/v1/positionSide/dual", signed=True)
    if bool(mode.get("dualSidePosition")):
        raise BinanceFuturesError(
            "В аккаунте включён Hedge Mode. MASB сейчас поддерживает только One-way Mode; "
            "переключите режим вручную в Binance после закрытия позиций."
        )


def _account_available_balance() -> float:
    account = _request("GET", "/fapi/v3/account", signed=True)
    return float(account.get("availableBalance", 0.0))


def _global_usage() -> tuple[int, set[str], float]:
    """Conservative global cap: count every non-zero Futures position and MASB entry order."""
    positions = _request("GET", "/fapi/v3/positionRisk", signed=True)
    open_orders = _request("GET", "/fapi/v1/openOrders", signed=True)
    active_symbols: set[str] = set()
    notional = 0.0
    for position in positions if isinstance(positions, list) else []:
        amount = float(position.get("positionAmt", 0.0))
        if abs(amount) > 0:
            active_symbols.add(str(position.get("symbol", "")))
            mark = float(position.get("markPrice", 0.0))
            notional += abs(amount) * mark
    for order in open_orders if isinstance(open_orders, list) else []:
        if order.get("status") in {"NEW", "PARTIALLY_FILLED"}:
            # Count manual/other-bot orders too: conservative symbol-level lock.
            active_symbols.add(str(order.get("symbol", "")))
    return len(active_symbols), active_symbols, notional


def _risk_quantity(balance: float, entry: float, stop: float, available_notional: float) -> tuple[float, float]:
    if balance <= 0 or entry <= 0 or stop <= 0 or entry == stop:
        raise BinanceFuturesError("Невозможно рассчитать позицию: проверьте баланс, Entry и SL")
    risk_budget = balance * RISK_PERCENT / 100.0
    risk_per_unit = abs(entry - stop) + (entry + stop) * COMMISSION_PER_SIDE
    raw_qty = risk_budget / risk_per_unit
    max_qty_by_leverage = max(0.0, available_notional) / entry
    quantity = min(raw_qty, max_qty_by_leverage)
    if quantity <= 0:
        raise BinanceFuturesError("Недостаточно свободного лимита номинала для новой позиции")
    estimated_risk = quantity * risk_per_unit
    return quantity, estimated_risk


def _cancel_algo(algo_id: Any) -> None:
    if algo_id:
        _request("DELETE", "/fapi/v1/algoOrder", {"algoId": algo_id}, signed=True)


def _emergency_close(symbol: str, direction: str) -> None:
    """Best-effort close if a protective order could not be installed."""
    positions = _request("GET", "/fapi/v3/positionRisk", {"symbol": symbol}, signed=True)
    for position in positions if isinstance(positions, list) else []:
        amount = float(position.get("positionAmt", 0.0))
        if abs(amount) <= 0:
            continue
        rules = _symbol_rules(symbol)
        quantity = _round_step(abs(amount), str(rules["stepSize"]))
        if _decimal(quantity) <= 0:
            raise BinanceFuturesError("Невозможно округлить аварийное закрытие позиции")
        _request("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": "SELL" if amount > 0 else "BUY",
            "type": "MARKET",
            "quantity": quantity,
            "reduceOnly": "true",
            "newOrderRespType": "RESULT",
        }, signed=True)
        logger.critical("Выполнено аварийное закрытие %s после ошибки установки SL/TP", symbol)



def _attach_protection(record: dict[str, Any]) -> dict[str, Any]:
    """Place exchange SL/TP after entry is filled (position exists)."""
    symbol = str(record["symbol"])
    direction = str(record["direction"]).upper()
    exit_side = "SELL" if direction == "LONG" else "BUY"
    sl_price = _round_step(float(record["sl"]), str(_symbol_rules(symbol)["tickSize"]), ROUND_HALF_UP)
    tp_price = _round_step(float(record["tp"]), str(_symbol_rules(symbol)["tickSize"]), ROUND_HALF_UP)
    suffix = f"{int(time.time() * 1000) % 10_000_000:07d}"
    sl_client_id = record.get("sl_client_order_id") or f"MASB_S_{suffix}"
    tp_client_id = record.get("tp_client_order_id") or f"MASB_T_{suffix}"
    updates: dict[str, Any] = {
        "sl_client_order_id": sl_client_id,
        "tp_client_order_id": tp_client_id,
    }
    if record.get("sl_algo_id"):
        updates["sl_algo_id"] = record["sl_algo_id"]
    else:
        try:
            sl_result = _algo_order(symbol, exit_side, "STOP_MARKET", sl_price, sl_client_id)
            updates["sl_algo_id"] = sl_result.get("algoId")
        except Exception:
            logger.exception("Не удалось поставить SL для %s после входа", symbol)
            updates["protect_error"] = "sl_failed"
            return updates
    if record.get("tp_algo_id"):
        updates["tp_algo_id"] = record["tp_algo_id"]
        updates["protect_pending"] = False
    else:
        try:
            tp_result = _algo_order(symbol, exit_side, "TAKE_PROFIT_MARKET", tp_price, tp_client_id)
            updates["tp_algo_id"] = tp_result.get("algoId")
            updates["protect_pending"] = False
            updates.pop("protect_error", None)
        except Exception:
            logger.exception("Не удалось поставить TP для %s после входа", symbol)
            updates["protect_error"] = "tp_failed"
            updates["protect_pending"] = True  # будет повторено в sync_real_order_states
    return updates


def _algo_order(symbol: str, side: str, order_type: str, trigger_price: str, client_id: str) -> dict[str, Any]:
    return _request("POST", "/fapi/v1/algoOrder", {
        "algoType": "CONDITIONAL",
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "triggerPrice": trigger_price,
        "closePosition": "true",
        "workingType": "MARK_PRICE",
        "priceProtect": "TRUE",
        "clientAlgoId": client_id,
    }, signed=True)


def submit_signal_order(strategy: str, symbol: str, signal: dict[str, Any], candle_timestamp: int) -> dict[str, Any]:
    """Submit one real LIMIT entry and exchange-side protective SL/TP orders.

    Per-strategy REAL toggle. At most one position/order per symbol and two
    occupied symbols globally. Live execution requires explicit .env opt-in.
    """
    if not strategy_real_enabled(strategy):
        raise BinanceFuturesError(
            f"REAL запрещён для стратегии {strategy}. Включите REAL для неё в меню 🧩 Стратегии."
        )
    if not real_trading_enabled():
        raise BinanceFuturesError("REAL-торговля выключена: установите MASB_REAL_TRADING_ENABLED=true локально")
    orders = signal.get("orders") or []
    if not orders:
        raise BinanceFuturesError("В сигнале нет ордеров")

    symbol = symbol.upper().strip()
    if not symbol.endswith("USDT"):
        raise BinanceFuturesError("Разрешены только USDT-M Futures пары")

    # Two fib entries represent scaling into one symbol position. To avoid
    # averaging/overlapping protection orders in One-way Mode, only entry #1
    # is executed live; both remain visible in the DEMO strategy analysis.
    order = sorted(orders, key=lambda item: int(item.get("entry_number", 1)))[0]
    entry = float(order["entry"])
    tp = float(order["tp"])
    sl = float(order["sl"])
    direction = str(order.get("direction", signal.get("direction", ""))).upper()
    if direction not in {"LONG", "SHORT"}:
        raise BinanceFuturesError("Неизвестное направление сделки")
    if not all(math.isfinite(x) and x > 0 for x in (entry, tp, sl)):
        raise BinanceFuturesError("Entry/TP/SL содержат некорректные цены")
    if direction == "LONG" and not (sl < entry < tp):
        raise BinanceFuturesError("Для LONG ожидается SL < Entry < TP")
    if direction == "SHORT" and not (tp < entry < sl):
        raise BinanceFuturesError("Для SHORT ожидается TP < Entry < SL")

    with _LOCK:
        _credentials()
        _assert_one_way_mode()
        used, occupied_symbols, current_notional = _global_usage()
        if symbol in occupied_symbols:
            raise BinanceFuturesError(f"{symbol}: уже есть открытая позиция или ожидающий REAL-вход")
        if used >= MAX_OPEN_POSITIONS:
            raise BinanceFuturesError(f"Достигнут глобальный лимит: {MAX_OPEN_POSITIONS} активные позиции/символа")

        # Fixed margin + fixed leverage for strategy REAL trades.
        # qty = (margin * leverage) / entry
        margin_usdt = _strategy_margin_usdt()
        lev = MAX_LEVERAGE  # 5x
        available_balance = _account_available_balance()
        if margin_usdt > available_balance:
            raise BinanceFuturesError(
                f"Недостаточно баланса для стратегии: нужно {margin_usdt:.2f} USDT маржи, "
                f"доступно {available_balance:.2f} USDT"
            )

        rules = _symbol_rules(symbol)
        if rules.get("status") != "TRADING" or rules.get("quoteAsset") != "USDT":
            raise BinanceFuturesError(f"{symbol} не является активной USDT-M Futures парой")
        entry_rounding = ROUND_DOWN if direction == "LONG" else ROUND_UP
        entry_price = _round_step(entry, str(rules["tickSize"]), entry_rounding)
        sl_rounding = ROUND_DOWN if direction == "LONG" else ROUND_UP
        tp_rounding = ROUND_UP if direction == "LONG" else ROUND_DOWN
        sl_price = _round_step(sl, str(rules["tickSize"]), sl_rounding)
        tp_price = _round_step(tp, str(rules["tickSize"]), tp_rounding)
        if direction == "LONG" and not (float(sl_price) < float(entry_price) < float(tp_price)):
            raise BinanceFuturesError("После округления по tickSize нарушился порядок SL/Entry/TP")
        if direction == "SHORT" and not (float(tp_price) < float(entry_price) < float(sl_price)):
            raise BinanceFuturesError("После округления по tickSize нарушился порядок TP/Entry/SL")

        notional = margin_usdt * lev
        raw_qty = notional / float(entry_price)
        quantity = _round_step(raw_qty, str(rules["stepSize"]))
        qty_dec = _decimal(quantity)
        min_qty = _decimal(rules["minQty"])
        min_notional = _decimal(rules.get("minNotional", "5"))
        if qty_dec <= 0 or qty_dec < min_qty or (qty_dec * _decimal(entry_price)) < min_notional:
            need_qty = max(float(min_qty), float(min_notional) / float(entry_price))
            need_qty = float(_decimal(_round_step(need_qty, str(rules["stepSize"]), ROUND_UP)))
            need_margin = round((need_qty * float(entry_price) / lev) * 1.02 + 0.01, 2)
            raise BinanceFuturesError(
                f"Маржа {margin_usdt} USDT при {lev}x слишком мала для {symbol}. "
                f"Поставьте MASB_STRATEGY_MARGIN_USDT≥{need_margin} в .env"
            )
        if qty_dec > _decimal(rules["maxQty"]):
            raise BinanceFuturesError(f"Количество превышает максимум Binance для {symbol}")

        estimated_risk = float(qty_dec) * (
            abs(float(entry_price) - float(sl_price))
            + (float(entry_price) + float(sl_price)) * COMMISSION_PER_SIDE
        )
        side = "BUY" if direction == "LONG" else "SELL"
        exit_side = "SELL" if direction == "LONG" else "BUY"
        suffix = hashlib.sha1(f"{strategy}|{symbol}|{candle_timestamp}|{order.get('entry_number')}".encode()).hexdigest()[:10].upper()
        entry_client_id = f"MASB_E_{suffix}"
        sl_client_id = f"MASB_S_{suffix}"
        tp_client_id = f"MASB_T_{suffix}"

        # Set symbol leverage to 5x, never change margin type automatically.
        _request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": MAX_LEVERAGE}, signed=True)
        entry_result = _request("POST", "/fapi/v1/order", {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": quantity,
            "price": entry_price,
            "newClientOrderId": entry_client_id,
            "newOrderRespType": "RESULT",
        }, signed=True)

        # SL/TP only after position exists (Binance error -4509 otherwise)
        sl_algo_id = None
        tp_algo_id = None
        protect_pending = True
        status = "PENDING"
        entry_status = str(entry_result.get("status", ""))
        if entry_status in {"FILLED", "PARTIALLY_FILLED"}:
            try:
                prot = _attach_protection({
                    "symbol": symbol,
                    "direction": direction,
                    "sl": float(sl_price),
                    "tp": float(tp_price),
                    "sl_client_order_id": sl_client_id,
                    "tp_client_order_id": tp_client_id,
                })
                sl_algo_id = prot.get("sl_algo_id")
                tp_algo_id = prot.get("tp_algo_id")
                protect_pending = bool(prot.get("protect_pending", False))
                if sl_algo_id:
                    status = "OPEN"
            except Exception:
                logger.exception("Не удалось сразу навесить SL/TP после fill %s", symbol)

        record = {
            "strategy": strategy,
            "symbol": symbol,
            "direction": direction,
            "status": status,
            "created_at": int(time.time()),
            "entry": float(entry_price),
            "tp": float(tp_price),
            "sl": float(sl_price),
            "quantity": float(quantity),
            "estimated_risk_with_fees": float(estimated_risk),
            "margin_usdt": float(margin_usdt),
            "notional": float(notional),
            "risk_budget": float(margin_usdt),
            "leverage": lev,
            "entry_order_id": entry_result.get("orderId"),
            "entry_client_order_id": entry_client_id,
            "sl_client_order_id": sl_client_id,
            "tp_client_order_id": tp_client_id,
            "sl_algo_id": sl_algo_id,
            "tp_algo_id": tp_algo_id,
            "protect_pending": protect_pending,
            "entry_number": int(order.get("entry_number", 1)),
            "candle_timestamp": int(candle_timestamp),
            # Данные для графика: зигзаг и уровни Фибоначчи (как в демо-сделках).
            "entry_fib": float(order.get("entry_fib", 0.0) or 0.0),
            "tp_fib": float(order.get("tp_fib", 0.0) or 0.0),
            "is_green_tp": bool(order.get("is_green_tp", False)),
            "swing_start": _num(signal.get("swing_start")),
            "swing_end": _num(signal.get("swing_end")),
            "swing_start_index": int(signal.get("swing_start_index", 0) or 0),
            "swing_end_index": int(signal.get("swing_end_index", 0) or 0),
            "swing_start_timestamp": signal.get("swing_start_timestamp"),
            "swing_end_timestamp": signal.get("swing_end_timestamp"),
            "fibonacci": dict(signal.get("fibonacci") or {}),
            "green_fibonacci": dict(signal.get("green_fibonacci") or {}),
        }
        state = _read_state()
        state["orders"].append(record)
        state["orders"] = state["orders"][-1000:]
        _write_state(state)
        return record



def submit_manual_order(
    symbol: str,
    direction: str,
    entry: float,
    tp: float,
    sl: float,
    leverage: int,
    margin_usdt: float,
) -> dict[str, Any]:
    """Place a manual LIMIT entry + exchange SL/TP on Binance Futures (REAL).

    Position size is calculated from user margin and leverage:
    notional = margin_usdt * leverage
    quantity = notional / entry
    """
    if not real_trading_enabled():
        raise BinanceFuturesError(
            "REAL-торговля выключена: установите MASB_REAL_TRADING_ENABLED=true в .env"
        )

    symbol = symbol.upper().strip()
    direction = direction.upper().strip()
    if not symbol.endswith("USDT"):
        raise BinanceFuturesError("Разрешены только USDT-M Futures пары")
    if direction not in {"LONG", "SHORT"}:
        raise BinanceFuturesError("Направление должно быть LONG или SHORT")
    if not all(math.isfinite(x) and x > 0 for x in (entry, tp, sl, margin_usdt)):
        raise BinanceFuturesError("Entry / TP / SL / маржа должны быть положительными числами")
    if direction == "LONG" and not (sl < entry < tp):
        raise BinanceFuturesError("Для LONG ожидается SL < Entry < TP")
    if direction == "SHORT" and not (tp < entry < sl):
        raise BinanceFuturesError("Для SHORT ожидается TP < Entry < SL")

    lev = int(leverage)
    if lev < 1 or lev > 50:
        raise BinanceFuturesError("Плечо должно быть от 1 до 50")
    max_allowed = int(os.getenv("MASB_MANUAL_MAX_LEVERAGE", "20"))
    if lev > max_allowed:
        raise BinanceFuturesError(f"Плечо ограничено {max_allowed}x (MASB_MANUAL_MAX_LEVERAGE)")

    max_margin = float(os.getenv("MASB_MANUAL_MAX_MARGIN_USDT", "500"))
    if margin_usdt > max_margin:
        raise BinanceFuturesError(
            f"Маржа ограничена {max_margin} USDT (MASB_MANUAL_MAX_MARGIN_USDT)"
        )
    if margin_usdt < 1:
        raise BinanceFuturesError("Минимальная маржа — 1 USDT")

    with _LOCK:
        _credentials()
        _assert_one_way_mode()
        used, occupied_symbols, current_notional = _global_usage()
        if symbol in occupied_symbols:
            raise BinanceFuturesError(f"{symbol}: уже есть открытая позиция или ожидающий REAL-вход")
        if used >= MAX_OPEN_POSITIONS:
            raise BinanceFuturesError(
                f"Достигнут глобальный лимит: {MAX_OPEN_POSITIONS} активные позиции/символа"
            )

        available_balance = _account_available_balance()
        if margin_usdt > available_balance:
            raise BinanceFuturesError(
                f"Недостаточно свободного баланса: нужно {margin_usdt:.2f} USDT, "
                f"доступно {available_balance:.2f} USDT"
            )

        rules = _symbol_rules(symbol)
        step = str(rules["stepSize"])
        min_qty = float(rules.get("minQty") or step)
        min_notional = float(rules.get("minNotional") or 5)

        notional = margin_usdt * lev
        raw_qty = notional / entry
        quantity = _round_step(raw_qty, step)
        qty_f = float(_decimal(quantity))

        # If rounding wiped the size, or below exchange minimums — tell exact min margin
        if qty_f <= 0 or qty_f < min_qty or (qty_f * entry) < min_notional:
            need_qty = max(min_qty, min_notional / entry)
            # round need_qty UP to step so order is valid
            need_qty_str = _round_step(need_qty, step, ROUND_UP)
            need_qty = float(_decimal(need_qty_str))
            if need_qty <= 0:
                need_qty = min_qty
            need_notional = need_qty * entry
            need_margin = need_notional / lev
            # small buffer for float/rounding
            need_margin = round(need_margin * 1.02 + 0.01, 2)
            raise BinanceFuturesError(
                "Размер позиции слишком маленький для этой монеты.\n"
                f"step={step}, minQty={min_qty}, minNotional≈{min_notional} USDT.\n"
                f"При Entry={entry} и плече {lev}x поставьте маржу минимум ≈ {need_margin} USDT "
                f"(номинал ≈ {need_notional:.2f} USDT)."
            )

        # Estimate risk at SL (for display / logging)
        risk_per_unit = abs(entry - sl) + (entry + sl) * COMMISSION_PER_SIDE
        estimated_risk = qty_f * risk_per_unit

        entry_price = _round_step(entry, str(rules["tickSize"]), ROUND_HALF_UP)
        tp_price = _round_step(tp, str(rules["tickSize"]), ROUND_HALF_UP)
        sl_price = _round_step(sl, str(rules["tickSize"]), ROUND_HALF_UP)

        side = "BUY" if direction == "LONG" else "SELL"
        exit_side = "SELL" if direction == "LONG" else "BUY"

        suffix = f"{int(time.time() * 1000) % 10_000_000:07d}"
        entry_client_id = f"MASB_M_E_{suffix}"
        sl_client_id = f"MASB_M_S_{suffix}"
        tp_client_id = f"MASB_M_T_{suffix}"

        # Set requested leverage
        _request(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": lev},
            signed=True,
        )

        # Only LIMIT entry now. SL/TP with closePosition require an open position
        # on Binance (-4509 if placed before fill). Protective orders are attached
        # in sync_real_order_states() once the entry fills.
        entry_result = _request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": quantity,
                "price": entry_price,
                "newClientOrderId": entry_client_id,
                "newOrderRespType": "RESULT",
            },
            signed=True,
        )

        sl_algo_id = None
        tp_algo_id = None
        protect_pending = True
        status = "PENDING"
        # If limit filled immediately — attach protection now
        entry_status = str(entry_result.get("status", ""))
        if entry_status in {"FILLED", "PARTIALLY_FILLED"}:
            try:
                prot = _attach_protection({
                    "symbol": symbol,
                    "direction": direction,
                    "sl": float(sl_price),
                    "tp": float(tp_price),
                    "sl_client_order_id": sl_client_id,
                    "tp_client_order_id": tp_client_id,
                })
                sl_algo_id = prot.get("sl_algo_id")
                tp_algo_id = prot.get("tp_algo_id")
                protect_pending = bool(prot.get("protect_pending", False))
                if sl_algo_id:
                    status = "OPEN"
            except Exception:
                logger.exception("Не удалось сразу навесить SL/TP после мгновенного fill %s", symbol)

        record = {
            "strategy": "MANUAL",
            "symbol": symbol,
            "direction": direction,
            "status": "PENDING",
            "created_at": int(time.time()),
            "entry": float(entry_price),
            "tp": float(tp_price),
            "sl": float(sl_price),
            "quantity": float(quantity),
            "estimated_risk_with_fees": float(estimated_risk),
            "margin_usdt": float(margin_usdt),
            "notional": float(notional),
            "risk_budget": float(margin_usdt),
            "leverage": lev,
            "entry_order_id": entry_result.get("orderId"),
            "entry_client_order_id": entry_client_id,
            "sl_client_order_id": sl_client_id,
            "tp_client_order_id": tp_client_id,
            "sl_algo_id": sl_algo_id,
            "tp_algo_id": tp_algo_id,
            "protect_pending": protect_pending,
            "entry_number": 1,
            "candle_timestamp": 0,
            "manual": True,
        }
        # status may already be OPEN if filled instantly
        record["status"] = status
        state = _read_state()
        state["orders"].append(record)
        state["orders"] = state["orders"][-1000:]
        _write_state(state)
        return record


def get_real_order_history() -> list[dict[str, Any]]:
    with _LOCK:
        return list(_read_state().get("orders", []))


def sync_real_order_states() -> int:
    """Reconcile MASB limit entries against exchange positions and cancel stale protection."""
    if not real_trading_enabled():
        return 0
    changed = 0
    with _LOCK:
        records = get_real_order_history()
        if not records:
            return 0
        positions = _request("GET", "/fapi/v3/positionRisk", signed=True)
        open_positions = {
            str(item.get("symbol")): float(item.get("positionAmt", 0.0))
            for item in positions if isinstance(positions, list)
        }
        for record in records:
            if record.get("status") not in {"PENDING", "OPEN"}:
                continue
            symbol = str(record.get("symbol", ""))
            position_amount = open_positions.get(symbol, 0.0)
            updates: dict[str, Any] = {}
            if record.get("status") == "PENDING":
                try:
                    order = _request("GET", "/fapi/v1/order", {
                        "symbol": symbol,
                        "origClientOrderId": record.get("entry_client_order_id"),
                    }, signed=True)
                    order_status = str(order.get("status", ""))
                    if order_status in {"FILLED", "PARTIALLY_FILLED"} and abs(position_amount) > 0:
                        updates["status"] = "OPEN"
                        updates["filled_quantity"] = float(order.get("executedQty", 0.0))
                        # Attach SL/TP only after position exists (Binance -4509 otherwise)
                        if record.get("protect_pending") or not record.get("sl_algo_id"):
                            try:
                                updates.update(_attach_protection({**record, **updates}))
                            except Exception:
                                logger.exception(
                                    "Не удалось навесить SL/TP на %s после исполнения входа",
                                    symbol,
                                )
                    elif order_status in {"FILLED", "PARTIALLY_FILLED"} and abs(position_amount) == 0:
                        # The position may have hit SL/TP between monitor polls.
                        updates["status"] = "CLOSED"
                        updates["closed_at"] = int(time.time())
                        try:
                            _request("DELETE", "/fapi/v1/order", {
                                "symbol": symbol,
                                "origClientOrderId": record.get("entry_client_order_id"),
                            }, signed=True)
                        except Exception:
                            pass
                        for algo_id_key in ("sl_algo_id", "tp_algo_id"):
                            try:
                                _cancel_algo(record.get(algo_id_key))
                            except Exception:
                                logger.exception("Не удалось удалить защитную заявку после закрытия позиции")
                    elif order_status in {"CANCELED", "EXPIRED", "REJECTED"} and abs(position_amount) == 0:
                        updates["status"] = "CANCELED"
                        updates["exchange_order_status"] = order_status
                        for algo_id_key in ("sl_algo_id", "tp_algo_id"):
                            try:
                                _cancel_algo(record.get(algo_id_key))
                            except Exception:
                                logger.exception("Не удалось удалить защитную заявку после отмены входа")
                except BinanceFuturesError as exc:
                    # -2013 means order not found; do not silently assume it filled.
                    logger.warning("Не удалось сверить REAL-ордер %s: %s", record.get("entry_client_order_id"), exc)
            elif (
                record.get("status") == "OPEN"
                and abs(position_amount) > 0
                and (record.get("protect_pending") or not record.get("sl_algo_id") or not record.get("tp_algo_id"))
            ):
                # Позиция есть, но SL/TP не полностью выставлены — повторяем.
                try:
                    updates.update(_attach_protection(record))
                except Exception:
                    logger.exception("Не удалось повторно навесить SL/TP на %s", symbol)
            elif record.get("status") == "OPEN" and abs(position_amount) == 0:
                # Position has been closed by TP/SL or manually. Remove the other
                # conditional trigger and any unfilled remainder of the entry.
                updates["status"] = "CLOSED"
                updates["closed_at"] = int(time.time())
                try:
                    _request("DELETE", "/fapi/v1/order", {
                        "symbol": symbol,
                        "origClientOrderId": record.get("entry_client_order_id"),
                    }, signed=True)
                except Exception:
                    pass
                for algo_id_key in ("sl_algo_id", "tp_algo_id"):
                    algo_id = record.get(algo_id_key)
                    if algo_id:
                        try:
                            _cancel_algo(algo_id)
                        except Exception:
                            logger.exception("Не удалось очистить algo order %s", algo_id)
            if updates:
                state = _read_state()
                for stored in state["orders"]:
                    if stored.get("entry_client_order_id") == record.get("entry_client_order_id"):
                        stored.update(updates)
                        changed += 1
                        break
                _write_state(state)
    return changed
