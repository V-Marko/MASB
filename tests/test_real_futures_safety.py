from __future__ import annotations

import os

import pytest

from services import binance_futures as bf
from strategy.fibZZ10 import calculate_position


def test_real_trading_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MASB_REAL_TRADING_ENABLED", raising=False)
    assert bf.real_trading_enabled() is False


def test_only_fibzz10_is_allowlisted():
    assert bf.STRATEGY_ALLOWLIST == {"fibZZ10"}


def test_non_allowlisted_strategy_is_rejected_before_network(monkeypatch):
    monkeypatch.setenv("MASB_REAL_TRADING_ENABLED", "true")
    with pytest.raises(bf.BinanceFuturesError, match="REAL запрещён"):
        bf.submit_signal_order("fibZZ5", "BTCUSDT", {"orders": [{}]}, 123)


def test_risk_quantity_includes_entry_and_exit_fees():
    quantity, risk = bf._risk_quantity(100.0, 100.0, 97.0, 10000.0)
    expected = 1.0 / (3.0 + (100.0 + 97.0) * bf.COMMISSION_PER_SIDE)
    assert quantity == pytest.approx(expected)
    assert risk == pytest.approx(1.0)


def test_risk_quantity_respects_total_notional_cap():
    quantity, risk = bf._risk_quantity(100.0, 100.0, 99.99, 250.0)
    assert quantity == pytest.approx(2.5)
    assert quantity * 100.0 <= 250.0
    assert risk < 1.0


def test_quantity_rounds_down_to_exchange_step():
    assert bf._round_step(1.239, "0.01") == "1.23"


def test_fibzz10_demo_position_uses_one_percent_risk_including_fees():
    result = calculate_position(100.0, 100.0, 97.0)
    assert result["risk_budget"] == pytest.approx(1.0)
    assert result["estimated_total_risk"] <= 1.0 + 1e-10
    assert result["position_value"] <= 500.0
    assert result["estimated_entry_fee"] > 0
    assert result["estimated_exit_fee"] > 0


def test_submit_uses_first_entry_and_records_protective_orders(monkeypatch):
    monkeypatch.setattr(bf, "real_trading_enabled", lambda: True)
    monkeypatch.setattr(bf, "_credentials", lambda: ("key", "secret"))
    monkeypatch.setattr(bf, "_assert_one_way_mode", lambda: None)
    monkeypatch.setattr(bf, "_global_usage", lambda: (0, set(), 0.0))
    monkeypatch.setattr(bf, "_account_available_balance", lambda: 100.0)
    monkeypatch.setattr(bf, "_symbol_rules", lambda symbol: {
        "status": "TRADING", "quoteAsset": "USDT", "stepSize": "0.001",
        "minQty": "0.001", "maxQty": "100000", "tickSize": "0.1", "minNotional": "5",
    })
    requests = []

    def fake_request(method, path, params=None, *, signed=False):
        requests.append((method, path, params, signed))
        if path == "/fapi/v1/order":
            return {"orderId": 321}
        return {}

    algo_calls = []

    def fake_algo(symbol, side, order_type, trigger_price, client_id):
        algo_calls.append((symbol, side, order_type, trigger_price, client_id))
        return {"algoId": len(algo_calls) + 100}

    saved = {}
    monkeypatch.setattr(bf, "_request", fake_request)
    monkeypatch.setattr(bf, "_algo_order", fake_algo)
    monkeypatch.setattr(bf, "_read_state", lambda: {"schema_version": 1, "orders": []})
    monkeypatch.setattr(bf, "_write_state", lambda state: saved.update(state))

    signal = {
        "direction": "LONG",
        "orders": [
            {"entry_number": 1, "direction": "LONG", "entry": 100.0, "tp": 109.0, "sl": 97.0},
            {"entry_number": 2, "direction": "LONG", "entry": 102.0, "tp": 110.0, "sl": 99.0},
        ],
    }
    trade = bf.submit_signal_order("fibZZ10", "BTCUSDT", signal, 123456)

    assert trade["entry_number"] == 1
    assert trade["entry_order_id"] == 321
    assert trade["leverage"] == 5
    assert trade["estimated_risk_with_fees"] <= 1.0
    assert len(algo_calls) == 2
    assert {item[2] for item in algo_calls} == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    assert len(saved["orders"]) == 1


def test_submit_refuses_third_active_symbol(monkeypatch):
    monkeypatch.setattr(bf, "real_trading_enabled", lambda: True)
    monkeypatch.setattr(bf, "_credentials", lambda: ("key", "secret"))
    monkeypatch.setattr(bf, "_assert_one_way_mode", lambda: None)
    monkeypatch.setattr(bf, "_global_usage", lambda: (2, {"ETHUSDT", "SOLUSDT"}, 300.0))
    with pytest.raises(bf.BinanceFuturesError, match="глобальный лимит"):
        bf.submit_signal_order("fibZZ10", "BTCUSDT", {
            "direction": "LONG",
            "orders": [{"entry_number": 1, "direction": "LONG", "entry": 100, "tp": 109, "sl": 97}],
        }, 123)


def test_real_capital_is_capped_at_configured_100_usdt_by_default(monkeypatch):
    monkeypatch.delenv("MASB_REAL_CAPITAL_USDT", raising=False)
    assert bf._configured_capital() == pytest.approx(100.0)
