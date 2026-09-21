"""Self-check (D124): with stock_simulator configured, no other broker's env token reaches the auth DB."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import broker_env_sync as b  # noqa: E402


def test_indmoney_token_never_synced_under_stock_simulator(monkeypatch):
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/stock_simulator/callback")
    for broker in ("indmoney", "stock_simulator", None):
        r = b.sync_env_secret_to_auth_db(broker=broker, reload_env=False)  # would hit the DB if not skipped
        assert r["reason"] == "stock_simulator_owns_session" and not r["synced"]
