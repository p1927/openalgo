"""One margin rule: the simulator estimate IS the sandbox reservation (Q3, D306/D410)."""

from __future__ import annotations

import math
import os
import random
import sys
from decimal import Decimal
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from broker.stock_simulator.api import margin_api  # noqa: E402
from sandbox import fund_manager as fm_mod  # noqa: E402

LOT = 75
STRIKE = 24000.0
SYMS = {
    ("NIFTY28MAR2424000CE", "NFO"): SimpleNamespace(strike=STRIKE, lotsize=LOT),
    ("NIFTY28MAR2424000PE", "NFO"): SimpleNamespace(strike=STRIKE, lotsize=LOT),
    ("NIFTY28MAR24FUT", "NFO"): SimpleNamespace(strike=0.0, lotsize=LOT),
    ("NIFTYBAD28MAR24CE", "NFO"): SimpleNamespace(strike=0.0, lotsize=LOT),
    ("RELIANCE", "NSE"): SimpleNamespace(strike=0.0, lotsize=1),
}
CFG = {}


@pytest.fixture(autouse=True)
def sandbox_config(monkeypatch):
    CFG.clear()
    defaults = {
        "starting_capital": "10000000",
        "equity_mis_leverage": "5",
        "equity_cnc_leverage": "1",
        "futures_leverage": "10",
        "option_buy_leverage": "1",
        "option_sell_notional_leverage": "16",
    }
    monkeypatch.setattr(fm_mod, "get_config", lambda k, d=None: CFG.get(k, defaults.get(k, d)))
    monkeypatch.setattr(fm_mod, "get_symbol_info", lambda s, e: SYMS.get((s, e)))


def leg(symbol="NIFTY28MAR2424000CE", action="BUY", qty=LOT, price=100, product="NRML", exchange="NFO"):
    return {"symbol": symbol, "exchange": exchange, "action": action, "quantity": str(qty),
            "product": product, "pricetype": "LIMIT", "price": str(price)}


def estimate(*legs):
    resp, body = margin_api.calculate_margin_api(list(legs), "")
    return resp.status_code, body


def total(*legs):
    code, body = estimate(*legs)
    assert code == 200, body
    return body["data"]["total_margin_required"]


def test_long_option_is_premium():
    assert total(leg(action="BUY")) == 100 * LOT


def test_short_option_is_strike_notional_not_premium():
    # 24000 x 75 / 16 = 112,500 (about 1 lakh), not the 7,500 premium the old rule blocked
    assert total(leg(action="SELL")) == 112500.0


def test_buy_and_sell_same_strike_differ_put_and_call_agree():
    assert total(leg(action="SELL")) > 10 * total(leg(action="BUY"))
    assert total(leg("NIFTY28MAR2424000PE", "SELL")) == total(leg(action="SELL"))


def test_deep_otm_short_premium_does_not_shrink_the_margin():
    assert total(leg(action="SELL", price=2)) == total(leg(action="SELL", price=300))


def test_short_option_never_below_premium():
    assert total(leg(action="SELL", price=5000)) == 5000 * LOT  # premium above strike/16 per unit


def test_futures_same_both_sides_and_equity_mis():
    assert total(leg("NIFTY28MAR24FUT", "BUY", price=24000)) == 24000 * LOT / 10
    assert total(leg("NIFTY28MAR24FUT", "SELL", price=24000)) == 24000 * LOT / 10
    assert total(leg("RELIANCE", "BUY", 10, 1500, "MIS", "NSE")) == 3000


def test_lot_size_multiplication_is_linear():
    one = total(leg(action="SELL", qty=LOT))
    assert total(leg(action="SELL", qty=3 * LOT)) == 3 * one


def test_basket_is_the_sum_of_its_legs():
    assert total(leg(action="SELL"), leg(action="BUY")) == 112500.0 + 7500.0


def test_tuning_the_key_changes_the_short_margin():
    CFG["option_sell_notional_leverage"] = "8"
    assert total(leg(action="SELL")) == 225000.0


@pytest.mark.parametrize("price", ["0", "nan", "inf", "-5", "abc", None])
def test_unpriceable_leg_fails_loud_not_zero_or_floor(price, monkeypatch):
    import sandbox.execution_engine as ee

    monkeypatch.setattr(ee.ExecutionEngine, "_fetch_quote", lambda self, s, e: None)
    code, body = estimate(leg(price=price))
    assert code == 400 and body["status"] == "error"


def test_market_leg_uses_the_sandbox_quote(monkeypatch):
    import sandbox.execution_engine as ee

    monkeypatch.setattr(ee.ExecutionEngine, "_fetch_quote", lambda self, s, e: {"ltp": 80})
    assert total(leg(price=0, action="BUY")) == 80 * LOT


def test_unknown_symbol_missing_strike_and_bad_leverage_fail_loud():
    assert estimate(leg("NOPE", "BUY", 1, 10, "MIS", "NSE"))[0] == 400
    assert estimate(leg("NIFTYBAD28MAR24CE", "SELL"))[0] == 400
    CFG["option_sell_notional_leverage"] = "0"
    assert estimate(leg(action="SELL"))[0] == 400
    assert estimate(leg(action="BUY"))[0] == 200  # long options do not use the short key


def test_one_bad_leg_fails_the_whole_basket():
    assert estimate(leg(action="SELL"), leg(price="nan"))[0] == 400


def test_gate_estimate_equals_sandbox_reservation_for_random_orders():
    """For random orders the estimate equals what FundManager (the order path) reserves, leg by leg."""
    rng = random.Random(7)
    fm = fm_mod.FundManager("prop")
    for _ in range(300):
        symbol, exchange = rng.choice(list(SYMS)[:3] + [("RELIANCE", "NSE")])
        action = rng.choice(["BUY", "SELL"])
        qty = rng.randint(1, 8) * SYMS[(symbol, exchange)].lotsize
        price = round(rng.uniform(0.5, 30000), 2)
        product = rng.choice(["MIS", "NRML"] if exchange == "NSE" else ["NRML", "MIS"])
        reserved, _msg = fm.calculate_margin_required(symbol, exchange, product, qty, price, action)
        got = total(leg(symbol, action, qty, price, product, exchange))
        assert got == float(round(reserved, 2)), (symbol, action, qty, price, product)
        assert math.isfinite(got) and got > 0
        assert isinstance(reserved, Decimal)
