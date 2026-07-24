"""Tests for /auth/brokers and /auth/broker/prepare-connect."""

from __future__ import annotations

import os
import sys

import pytest
from flask import Flask, session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blueprints.auth as auth_module  # noqa: E402


@pytest.fixture()
def app():
    application = Flask(__name__)
    application.secret_key = "test-secret"
    application.register_blueprint(auth_module.auth_bp)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.mark.unit
def test_list_brokers_requires_session(client, monkeypatch):
    monkeypatch.setenv("VALID_BROKERS", "stock_simulator,indmoney")
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/stock_simulator/callback")

    response = client.get("/auth/brokers")
    assert response.status_code == 401


@pytest.mark.unit
def test_list_brokers_returns_stock_simulator(client, monkeypatch):
    monkeypatch.setenv("VALID_BROKERS", "stock_simulator,indmoney,alpaca")
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/stock_simulator/callback")

    with client.session_transaction() as sess:
        sess["user"] = "admin"

    response = client.get("/auth/brokers")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["default_broker"] == "stock_simulator"
    ids = {item["id"] for item in payload["brokers"]}
    assert "stock_simulator" in ids
    assert "indmoney" in ids
    assert "alpaca" in ids
    sim = next(item for item in payload["brokers"] if item["id"] == "stock_simulator")
    assert sim["display_name"] == "NSE Simulator (Replay)"


@pytest.mark.unit
def test_prepare_connect_stock_simulator(client, monkeypatch):
    monkeypatch.setenv("VALID_BROKERS", "stock_simulator")
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/stock_simulator/callback")
    monkeypatch.setenv("HOST_SERVER", "http://127.0.0.1:5001")

    with client.session_transaction() as sess:
        sess["user"] = "admin"

    response = client.post(
        "/auth/broker/prepare-connect",
        json={"broker": "stock_simulator"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["connect_url"] == "http://127.0.0.1:5001/stock_simulator/callback"
    assert payload["auth_flow"] == "callback"


@pytest.mark.unit
def test_prepare_connect_rejects_unknown_broker(client, monkeypatch):
    monkeypatch.setenv("VALID_BROKERS", "stock_simulator")
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/stock_simulator/callback")

    with client.session_transaction() as sess:
        sess["user"] = "admin"

    response = client.post(
        "/auth/broker/prepare-connect",
        json={"broker": "not_installed"},
    )
    assert response.status_code == 400


@pytest.mark.unit
def test_list_brokers_clamps_default_when_redirect_broker_unavailable(client, monkeypatch):
    monkeypatch.setenv("VALID_BROKERS", "stock_simulator,indmoney")
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/zerodha/callback")

    with client.session_transaction() as sess:
        sess["user"] = "admin"

    response = client.get("/auth/brokers")
    assert response.status_code == 200
    payload = response.get_json()
    ids = {item["id"] for item in payload["brokers"]}
    assert payload["default_broker"] in ids
    assert payload["default_broker"] != "zerodha"


@pytest.mark.unit
def test_list_brokers_is_default_matches_clamped_default(client, monkeypatch):
    monkeypatch.setenv("VALID_BROKERS", "stock_simulator,indmoney")
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/zerodha/callback")

    with client.session_transaction() as sess:
        sess["user"] = "admin"

    response = client.get("/auth/brokers")
    payload = response.get_json()
    default_id = payload["default_broker"]
    defaults = [item for item in payload["brokers"] if item["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == default_id


@pytest.mark.unit
def test_prepare_connect_rejects_unconfigured_broker(client, monkeypatch):
    monkeypatch.setenv("VALID_BROKERS", "stock_simulator,dhan")
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/stock_simulator/callback")
    monkeypatch.delenv("DHAN_API_KEY", raising=False)
    monkeypatch.delenv("DHAN_API_SECRET", raising=False)
    monkeypatch.delenv("BROKER_API_KEY", raising=False)
    monkeypatch.delenv("BROKER_API_SECRET", raising=False)

    with client.session_transaction() as sess:
        sess["user"] = "admin"

    response = client.post(
        "/auth/broker/prepare-connect",
        json={"broker": "dhan"},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert "not configured" in payload["message"].lower()