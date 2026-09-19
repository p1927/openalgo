"""Self-check: stock_simulator no-op session reconnect touches nothing unless it is the configured broker."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import stock_simulator_session as s  # noqa: E402


def test_other_broker_is_left_alone(monkeypatch):
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/indmoney/callback")
    assert s.reconnect_if_configured("prat") is False  # would raise on DB access if it tried


def test_no_broker_configured_is_left_alone(monkeypatch):
    monkeypatch.delenv("REDIRECT_URL", raising=False)
    assert s.reconnect_if_configured("prat") is False
