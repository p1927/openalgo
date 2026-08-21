"""Tests for the OpenAlgo stock_simulator_control blueprint.

Since the standalone stock_simulator service (Phase 0/1-3 of
.claude/backlog/items/2026-08-21-stock-simulator-single-clock-source-of-truth.md),
this blueprint no longer owns a local ``ReplayService`` — it's a thin
forwarding proxy to ``StockSimulatorClient``. These tests exercise the
blueprint in isolation against a fake client so they don't need to boot the
full OpenAlgo stack (DB, broker, websocket proxy) or a real running service.
"""

from __future__ import annotations

import pytest


class _FakeClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _clock_payload(*, replay_date="2024-04-15", sim_now="2024-04-15T09:30:00+05:30", paused=False):
    return {
        "replay_date": replay_date,
        "sim_now": sim_now,
        "speed": 1.0,
        "loop": True,
        "stepped": False,
        "paused": paused,
        "session_open": True,
        "completed": False,
        "week_mode": False,
        "week_dates": [],
        "week_index": 0,
        "emit_interval_ms": 1000,
    }


class _FakeClient:
    """Duck-typed stand-in for ``StockSimulatorClient`` covering the surface
    the blueprint actually calls."""

    def __init__(self, *, replay_date: str = "2024-04-15") -> None:
        self._paused = False
        self._replay_date = replay_date

    def _status(self) -> dict:
        return {
            "mode": "replay",
            "clock": _clock_payload(replay_date=self._replay_date, paused=self._paused),
            "week_mode": False,
            "week_dates": [],
            "week_days_count": 5,
            "data_watermark": "2024-04-15",
            "options_source": None,
            "active_expiry": None,
            "available_dates": {"NIFTY": ["2024-04-15"], "BANKNIFTY": []},
            "hf_replay": True,
        }

    def status(self) -> dict:
        return self._status()

    def start_replay(self, date, *, end_date=None, speed=None, loop=None) -> dict:
        self._replay_date = date
        return self._status()

    def pause(self) -> dict:
        self._paused = True
        return self._status()

    def resume(self) -> dict:
        self._paused = False
        return self._status()

    def stop(self) -> dict:
        self._paused = True
        return {"message": "simulator stopped; consumers fall back to live", **self._status()}

    def calendar(self) -> dict:
        return {"days": [], "underlyings": ["NIFTY", "BANKNIFTY", "SENSEX"]}


@pytest.fixture
def fake_client():
    return _FakeClient()


@pytest.fixture
def control_app(monkeypatch, fake_client):
    """Build a minimal Flask app with the stock_simulator_control blueprint only."""
    from flask import Flask

    from openalgo.blueprints import stock_simulator_control as sc

    monkeypatch.setattr(sc, "_client", lambda: fake_client)
    monkeypatch.setattr(sc, "_require_control_token", lambda: None)

    app = Flask(__name__)
    app.register_blueprint(sc.stock_simulator_control_bp)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def control_token(monkeypatch) -> str:
    monkeypatch.setenv("SIMULATOR_CONTROL_TOKEN", "test-token-abc")
    return "test-token-abc"


def test_pause_returns_503_without_token(monkeypatch) -> None:
    from flask import Flask

    from openalgo.blueprints import stock_simulator_control as sc

    monkeypatch.delenv("SIMULATOR_CONTROL_TOKEN", raising=False)
    app = Flask(__name__)
    app.register_blueprint(sc.stock_simulator_control_bp)
    app.config["TESTING"] = True
    client = app.test_client()
    res = client.post("/stock_simulator/control/replay/pause")
    assert res.status_code == 503
    body = res.get_json()
    assert body["status"] == "error"
    assert "not configured" in body["message"]


def test_start_returns_400_for_missing_date(control_app, control_token) -> None:
    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/start",
        json={},
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 400
    assert "date is required" in res.get_json()["message"]


def test_start_forwards_to_client_and_returns_status(control_app, control_token, fake_client) -> None:
    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/start",
        json={"date": "2024-04-16", "end_date": "2024-04-19", "speed": 10, "loop": True},
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["clock"]["replay_date"] == "2024-04-16"
    assert fake_client._replay_date == "2024-04-16"


def _spy_mc_download(monkeypatch):
    """Replace ``async_master_contract_download`` with a no-op spy — see the
    original test file's docstring for why this must stay a no-op (avoids
    racing the real download thread against the rest of the suite)."""
    calls: list[tuple[str, ...]] = []
    from utils import auth_utils

    def _spy(broker: str) -> None:
        calls.append((broker,))
        return None

    monkeypatch.setattr(auth_utils, "async_master_contract_download", _spy)
    return calls


def test_start_triggers_mc_rebuild_when_replay_date_changes(control_app, control_token, monkeypatch) -> None:
    """Arming a NEW replay day kicks off async MC download in a thread —
    regression coverage for the server-to-server path staying in sync with
    the Sandbox UI's arm-replay behaviour."""
    calls = _spy_mc_download(monkeypatch)

    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/start",
        json={"date": "2024-04-20"},  # differs from fake_client's initial 2024-04-15
        headers={"X-Simulator-Control-Token": control_token},
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body.get("master_contract_refresh") == "completed"
    assert calls == [("stock_simulator",)]


def test_start_skips_mc_rebuild_when_replay_date_unchanged(control_app, control_token, monkeypatch) -> None:
    """Re-arming the SAME day does NOT trigger an MC rebuild."""
    calls = _spy_mc_download(monkeypatch)

    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/start",
        json={"date": "2024-04-15"},  # matches fake_client's initial replay_date
        headers={"X-Simulator-Control-Token": control_token},
    )

    assert res.status_code == 200
    body = res.get_json()
    assert "master_contract_refresh" not in body
    assert calls == []


def test_pause_returns_200_when_armed(control_app, control_token, fake_client) -> None:
    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/pause",
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["clock"]["paused"] is True
    assert fake_client._paused is True


def test_resume_returns_200_when_paused(control_app, control_token, fake_client) -> None:
    fake_client._paused = True
    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/resume",
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["clock"]["paused"] is False
    assert fake_client._paused is False


def test_stop_returns_200_and_message(control_app, control_token, fake_client) -> None:
    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/stop",
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert "simulator stopped" in body.get("message", "")
    assert fake_client._paused is True


def test_calendar_forwards_to_client(control_app, control_token) -> None:
    client = control_app.test_client()
    res = client.get(
        "/stock_simulator/control/replay/calendar",
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["days"] == []
    assert body["underlyings"] == ["NIFTY", "BANKNIFTY", "SENSEX"]


def test_client_error_propagates_status_code(control_app, control_token, fake_client, monkeypatch) -> None:
    from trade_integrations.stock_simulator.client import StockSimulatorClientError

    def _boom():
        raise StockSimulatorClientError("stock_simulator service unreachable", status_code=502)

    fake_client.status = _boom  # replay_status route calls c.status()
    client = control_app.test_client()
    res = client.get(
        "/stock_simulator/control/replay/status",
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 502
    assert res.get_json()["status"] == "error"
