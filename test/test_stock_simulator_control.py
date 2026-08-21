"""Tests for the OpenAlgo stock_simulator_control pause/resume/stop/calendar endpoints.

These cover the new wiring for the Replay calendar UI (Aug 2026):
- pause/resume/stop are token-gated and fail loud (503) when token unset.
- ``replay_calendar`` aggregates coverage flags + counts per union-of-days.
- All four endpoints return 200 with the simulator status payload on success.

We exercise the blueprint in isolation against a fake ``ReplayService`` so the
tests don't need to boot the full OpenAlgo stack (DB, broker, websocket proxy).
The catalog ``day_row_count`` method is unit-tested separately in
``tests/test_stock_simulator_clock.py``.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fake_service():
    """A duck-typed stand-in for ``ReplayService`` covering the surface the
    blueprint actually calls."""

    class _FakeClock:
        def __init__(self) -> None:
            self.paused = False
            self.completed = False

        def status(self) -> dict:
            return {
                "replay_date": "2024-04-15",
                "sim_now": "2024-04-15T09:30:00+05:30",
                "speed": 1.0,
                "loop": True,
                "stepped": False,
                "paused": self.paused,
                "session_open": True,
                "completed": self.completed,
                "week_mode": False,
                "week_dates": [],
                "week_index": 0,
            }

    class _FakeCatalog:
        def __init__(self, days: list[str]) -> None:
            self._days = days

        def available_dates(self, symbol: str, exchange: str) -> list[str]:
            return list(self._days)

        def day_row_count(self, symbol: str, exchange: str, day: str) -> int:
            return 0 if day not in self._days else 375

    class _FakeService:
        def __init__(self, days: list[str] | None = None) -> None:
            self.clock = _FakeClock()
            self._catalog = _FakeCatalog(days or [])

        def status(self) -> dict:
            return {
                "mode": "replay",
                "clock": self.clock.status(),
                "week_mode": False,
                "week_dates": [],
                "week_days_count": 5,
                "data_watermark": "2024-04-15",
                "options_source": None,
                "active_expiry": None,
                "available_dates": {"NIFTY": ["2024-04-15"], "BANKNIFTY": []},
                "hf_replay": True,
            }

        def pause(self) -> None:
            self.clock.paused = True

        def resume(self) -> None:
            self.clock.paused = False
            self.clock.completed = False

        def stop_simulator(self) -> None:
            self.clock.paused = True

        @property
        def catalog(self) -> _FakeCatalog:
            return self._catalog

    return _FakeService()


@pytest.fixture
def control_app(monkeypatch, fake_service):
    """Build a minimal Flask app with the stock_simulator_control blueprint only."""
    from flask import Flask

    from openalgo.blueprints import stock_simulator_control as sc

    monkeypatch.setattr(sc, "_get_replay_service", lambda *, reload=False: fake_service)
    # Patch _require_control_token to honour the env var; the real one is
    # already tested by the "no token" case below.
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


def test_start_returns_400_for_malformed_end_date(control_app, control_token) -> None:
    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/start",
        json={"date": "2024-04-15", "end_date": "not-a-date"},
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 400
    assert "end_date must be YYYY-MM-DD" in res.get_json()["message"]


def test_start_with_end_date_sets_range_env_and_persists(
    control_app, control_token, monkeypatch
) -> None:
    import os

    from database import sandbox_db

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        sandbox_db, "set_config", lambda key, value, **kw: calls.append((key, value))
    )
    monkeypatch.delenv("NSE_REPLAY_END_DATE", raising=False)

    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/start",
        json={"date": "2024-04-15", "end_date": "2024-04-19", "speed": 10, "loop": True},
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    assert os.environ["NSE_REPLAY_DATE"] == "2024-04-15"
    assert os.environ["NSE_REPLAY_END_DATE"] == "2024-04-19"
    persisted = dict(calls)
    assert persisted["sim_replay_date"] == "2024-04-15"
    assert persisted["sim_replay_end_date"] == "2024-04-19"


def test_start_without_end_date_clears_stale_range_env(
    control_app, control_token, monkeypatch
) -> None:
    """Arming a plain single day must not inherit a previously-armed range's
    end date left over in the process env."""
    import os

    from database import sandbox_db

    monkeypatch.setattr(sandbox_db, "set_config", lambda *a, **kw: True)
    monkeypatch.setenv("NSE_REPLAY_END_DATE", "2024-04-19")

    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/start",
        json={"date": "2024-04-15"},
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    assert "NSE_REPLAY_END_DATE" not in os.environ


def _spy_mc_download(monkeypatch):
    """Replace ``async_master_contract_download`` with a no-op spy.

    The endpoint does a lazy ``from utils.auth_utils import
    async_master_contract_download`` inside the new MC-rebuild block,
    so we patch the symbol in the source module's namespace — same
    trick the override tests use. Returns the spy list; append
    inspection lets the test assert whether (and when) the download
    was kicked off.

    Why a no-op (not a recording stub that calls the real function):
    the real ``async_master_contract_download`` spawns a daemon
    thread that imports trade_integrations and writes to the master
    contract status DB. Letting it run in tests would race with
    subsequent tests (the calendar test would see a transient
    "downloading" status and hit a half-initialized import). The
    spy records the call but does nothing, so the thread exits
    immediately and the test environment stays clean.
    """
    calls: list[tuple[str, ...]] = []
    from utils import auth_utils

    def _spy(broker: str) -> None:
        calls.append((broker,))
        # No-op: do NOT call the real function. The real function
        # spawns a daemon thread that touches the MC status DB and
        # imports trade_integrations, which races with the rest of
        # the test suite. The endpoint under test only cares that
        # ``async_master_contract_download`` was called with the
        # right argument; the body of the download is exercised by
        # ``test_stock_simulator_master_contract.py`` separately.
        return None

    monkeypatch.setattr(auth_utils, "async_master_contract_download", _spy)
    return calls


def test_start_triggers_mc_rebuild_when_replay_date_changes(
    control_app, control_token, monkeypatch
) -> None:
    """Arming a NEW replay day kicks off async MC download in a thread.

    Regression test for the bug where the server-to-server control
    endpoint didn't rebuild the symtoken table when the user moved
    from one replay day to another. The Sandbox UI path already
    did this; this test pins the control endpoint to the same
    behaviour so the two arm-replay paths can't drift.
    """
    import os

    from database import sandbox_db

    monkeypatch.setattr(sandbox_db, "set_config", lambda *a, **kw: True)
    monkeypatch.setenv("NSE_REPLAY_DATE", "2024-04-10")  # prior day
    calls = _spy_mc_download(monkeypatch)

    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/start",
        json={"date": "2024-04-15"},  # NEW day
        headers={"X-Simulator-Control-Token": control_token},
    )

    assert res.status_code == 200
    body = res.get_json()
    # The spy is a no-op, so the rebuild thread finishes well inside
    # the endpoint's 8s join timeout — the endpoint reports "completed",
    # not "started". "started" is only reported when the thread is
    # still alive after the join times out.
    assert body.get("master_contract_refresh") == "completed"
    assert os.environ["NSE_REPLAY_DATE"] == "2024-04-15"
    assert calls == [("stock_simulator",)]


def test_start_skips_mc_rebuild_when_replay_date_unchanged(
    control_app, control_token, monkeypatch
) -> None:
    """Re-arming the SAME day does NOT trigger an MC rebuild.

    Rebuilding on every start call would be wasteful and would race
    with any in-flight download from a previous arm. The trigger
    only fires when the date actually changes, matching the Sandbox
    UI's convention.
    """
    from database import sandbox_db

    monkeypatch.setattr(sandbox_db, "set_config", lambda *a, **kw: True)
    monkeypatch.setenv("NSE_REPLAY_DATE", "2024-04-15")  # same as new
    calls = _spy_mc_download(monkeypatch)

    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/start",
        json={"date": "2024-04-15"},
        headers={"X-Simulator-Control-Token": control_token},
    )

    assert res.status_code == 200
    body = res.get_json()
    assert "master_contract_refresh" not in body
    assert calls == []


def test_start_triggers_mc_rebuild_on_first_arm(
    control_app, control_token, monkeypatch
) -> None:
    """The very first arm (no prior ``NSE_REPLAY_DATE`` in env) DOES
    trigger an MC rebuild.

    Rationale: the symtoken table is empty, the new dropdown will
    show expiries from the bundle, and the chain call needs the
    matching (strike, expiry) rows. The sentinel default of
    ``"2021-03-25"`` in the prior-date read makes the
    ``new != prior`` check fire on first arm too. This mirrors the
    behaviour of the Sandbox UI's arm-replay path.
    """
    from database import sandbox_db

    monkeypatch.setattr(sandbox_db, "set_config", lambda *a, **kw: True)
    monkeypatch.delenv("NSE_REPLAY_DATE", raising=False)
    calls = _spy_mc_download(monkeypatch)

    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/start",
        json={"date": "2024-04-15"},
        headers={"X-Simulator-Control-Token": control_token},
    )

    assert res.status_code == 200
    body = res.get_json()
    # Spy is a no-op, so the rebuild thread finishes inside the
    # endpoint's 8s join timeout and reports "completed".
    assert body.get("master_contract_refresh") == "completed"
    assert calls == [("stock_simulator",)]


def test_pause_returns_200_when_armed(control_app, control_token, fake_service) -> None:
    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/pause",
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["clock"]["paused"] is True
    assert fake_service.clock.paused is True


def test_resume_returns_200_when_paused(control_app, control_token, fake_service) -> None:
    fake_service.clock.paused = True
    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/resume",
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["clock"]["paused"] is False
    assert fake_service.clock.paused is False


def test_stop_returns_200_and_message(control_app, control_token, fake_service) -> None:
    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/stop",
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert "simulator stopped" in body.get("message", "")
    assert fake_service.clock.paused is True


def test_stop_clears_persisted_sandbox_db_config(control_app, control_token, monkeypatch) -> None:
    """stop_replay() must clear sim_replay_* rows in sandbox_db, not just the
    in-process env var — otherwise hydrate_simulator_env_from_db() re-arms
    replay mode from stale config on the next OpenAlgo restart."""
    from database import sandbox_db

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sandbox_db, "set_config", lambda key, value, **kw: calls.append((key, value))
    )

    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/stop",
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200

    cleared_keys = {key for key, _value in calls}
    assert cleared_keys == {"sim_replay_date", "sim_replay_end_date", "sim_replay_speed", "sim_replay_loop"}
    assert all(value == "" for _key, value in calls)


def test_stop_survives_sandbox_db_write_failure(control_app, control_token, monkeypatch) -> None:
    """If sandbox_db writes fail, stop must still succeed (best-effort clear)."""
    from database import sandbox_db

    def _boom(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(sandbox_db, "set_config", _boom)

    client = control_app.test_client()
    res = client.post(
        "/stock_simulator/control/replay/stop",
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_calendar_returns_empty_days_when_no_data(
    control_app, control_token, monkeypatch, tmp_path
) -> None:
    """calendar() builds a fresh ReplayCatalog from env-driven data_root.

    Points ``NSE_REPLAY_DATA_ROOT`` at an empty temp dir — the dev machine's
    default data root has real recorded sessions, so without this the
    "no data" assumption is false and the test asserts against live data.

    Skipped if pandas/numpy are not importable in the test venv — that's an
    environment problem, not a code one. The endpoint shape is still covered
    by the surrounding blueprint tests.
    """
    try:
        import pandas  # noqa: F401
    except ImportError:
        pytest.skip("pandas not importable in this venv (numpy ABI mismatch)")
    monkeypatch.setenv("NSE_REPLAY_DATA_ROOT", str(tmp_path))
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


def test_calendar_returns_per_day_coverage(
    control_app, control_token, monkeypatch, tmp_path
) -> None:
    """calendar() now reads via stock_history.api.StockHistory.recorded_index_day_counts,
    which delegates to parquet_index_store.list_recorded_index_day_counts — the module
    that actually constructs ReplayCatalog today (it imports the class into its own
    namespace, so patching trade_integrations.stock_simulator.catalog.ReplayCatalog no
    longer has any effect on this call path — patch it where it's actually bound).

    Also pins NSE_REPLAY_DATA_ROOT at a unique tmp dir, both so the fake catalog's
    result isn't shadowed by any real recorded data on the dev machine, and so this
    test's cache key in parquet_index_store's mtime-keyed cache can't collide with
    another test using the same data_root string.
    """
    try:
        import pandas  # noqa: F401
    except ImportError:
        pytest.skip("pandas not importable in this venv (numpy ABI mismatch)")
    monkeypatch.setenv("NSE_REPLAY_DATA_ROOT", str(tmp_path))

    class _CatalogWithRows:
        def __init__(self, data_root) -> None:
            self.data_root = data_root

        def available_dates(self, symbol: str, exchange: str) -> list[str]:
            if symbol == "NIFTY":
                return ["2024-04-15"]
            if symbol == "BANKNIFTY":
                return ["2024-04-15"]
            return []

        def day_row_count(self, symbol: str, exchange: str, day: str) -> int:
            if symbol == "NIFTY" and day == "2024-04-15":
                return 375
            if symbol == "BANKNIFTY" and day == "2024-04-15":
                return 180
            return 0

        def day_counts(self, symbol: str, exchange: str) -> dict[str, int]:
            days = self.available_dates(symbol, exchange)
            return {day: self.day_row_count(symbol, exchange, day) for day in days}

    from trade_integrations.stock_history.store import parquet_index_store as store_mod

    monkeypatch.setattr(store_mod, "ReplayCatalog", _CatalogWithRows)
    store_mod._INDEX_DAY_COUNTS_CACHE.clear()

    client = control_app.test_client()
    res = client.get(
        "/stock_simulator/control/replay/calendar",
        headers={"X-Simulator-Control-Token": control_token},
    )
    assert res.status_code == 200
    body = res.get_json()
    rows = body["days"]
    assert len(rows) == 1
    row = rows[0]
    assert row["date"] == "2024-04-15"
    assert row["has_nifty"] is True
    assert row["has_banknifty"] is True
    assert row["has_sensex"] is False
    assert row["nifty_rows"] == 375
    assert row["banknifty_rows"] == 180
    assert row["sensex_rows"] == 0
