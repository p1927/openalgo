"""/api/trade-charges honours `market` (the Strategy Builder sends it): US legs get the flat per-contract
schedule from the one calculator, an unknown market is a 400. Auth/limiter/logging are stubbed."""

import importlib.util
import sys
import types
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
TRADE_INTEGRATIONS = ROOT.parent / "integrations"


def _client():
    def passthrough(fn=None, *a, **k):
        return fn if callable(fn) else (lambda f: f)

    stubs = {
        "limiter": types.SimpleNamespace(limiter=types.SimpleNamespace(limit=lambda *_a, **_k: (lambda f: f))),
        "utils.logging": types.SimpleNamespace(get_logger=lambda _n: types.SimpleNamespace(warning=lambda *a, **k: None)),
        "utils.session": types.SimpleNamespace(check_session_validity=passthrough),
        "broker.stock_simulator.api._trade_path": types.SimpleNamespace(
            ensure_trade_integrations_path=lambda: sys.path.insert(0, str(TRADE_INTEGRATIONS))
        ),
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    try:
        for name, mod in stubs.items():
            sys.modules[name] = mod
        spec = importlib.util.spec_from_file_location("trade_charges_under_test", ROOT / "blueprints" / "trade_charges.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(module.trade_charges_bp)
    return app.test_client()


LEGS = [{"side": "BUY", "option_type": "CE", "strike": 500.0, "price": 2.0, "symbol": "SPY", "quantity": 300}]


def test_us_market_gets_the_per_contract_schedule():
    res = _client().post("/api/trade-charges", json={"legs": LEGS, "market": "US", "broker_preset": "zerodha"})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["charges"]["total"]["total_charges"] == 1.95  # 3 contracts x 0.65, no STT/GST


def test_unknown_market_is_a_400():
    assert _client().post("/api/trade-charges", json={"legs": LEGS, "market": "EU"}).status_code == 400
