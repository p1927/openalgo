"""Segment O fixes in the Trade-owned fork code (docs: backlog 2026-10-14-r9-openalgo-fork-open).

Each fix has three or more variants: the case that was broken, the neighbours that must not change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# --- options multi-order: a leg can name its contract by symbol or strike, not only by offset -------------------


def _leg(**over):
    leg = {"option_type": "CE", "action": "SELL", "quantity": 75}
    leg.update(over)
    return leg


def _load_leg(**over):
    from restx_api.schemas import OptionsMultiOrderLegSchema

    return OptionsMultiOrderLegSchema().load(_leg(**over))


def test_leg_with_only_an_explicit_symbol_is_accepted():
    assert _load_leg(symbol="NIFTY24JUL2524500CE")["symbol"] == "NIFTY24JUL2524500CE"


def test_leg_with_only_a_strike_is_accepted():
    assert _load_leg(strike=24500)["strike"] == 24500.0


def test_leg_with_only_an_offset_still_works():
    assert _load_leg(offset="OTM5")["offset"] == "OTM5"


def test_leg_naming_no_contract_is_rejected():
    from marshmallow import ValidationError

    with pytest.raises(ValidationError) as err:
        _load_leg()
    assert "offset" in err.value.messages


@pytest.mark.parametrize("bad", [0, -100])
def test_leg_with_a_non_positive_strike_is_rejected(bad):
    from marshmallow import ValidationError

    with pytest.raises(ValidationError):
        _load_leg(strike=bad)


def test_leg_with_an_unknown_field_is_still_rejected():
    from marshmallow import ValidationError

    with pytest.raises(ValidationError):
        _load_leg(offset="ATM", nonsense=1)


def test_whole_request_carries_a_symbol_close_leg():
    from restx_api.schemas import OptionsMultiOrderSchema

    body = {
        "apikey": "k",
        "strategy": "close",
        "underlying": "NIFTY",
        "exchange": "NSE_INDEX",
        "legs": [_leg(symbol="NIFTY24JUL2524500CE", action="BUY")],
    }
    assert OptionsMultiOrderSchema().load(body)["legs"][0]["symbol"] == "NIFTY24JUL2524500CE"


# --- chartink: the in-memory square-off schedule is rebuilt from the database ---------------------------------


@pytest.fixture
def chartink(monkeypatch):
    import blueprints.chartink as ck

    rows = {}
    monkeypatch.setattr(ck, "get_all_strategies", lambda: list(rows.values()))
    monkeypatch.setattr(ck, "get_strategy", lambda sid: rows.get(sid))
    ck.rows = rows
    yield ck
    for job in ck.scheduler.get_jobs():
        if job.id.startswith("squareoff_"):
            ck.scheduler.remove_job(job.id)
    del ck.rows


def _strategy(sid, *, intraday=True, squareoff="15:15"):
    return SimpleNamespace(id=sid, is_intraday=intraday, squareoff_time=squareoff)


def test_restore_installs_a_job_per_intraday_strategy(chartink):
    chartink.rows.update({1: _strategy(1), 2: _strategy(2, squareoff="14:45")})
    assert chartink.restore_squareoff_jobs() == 2
    job = chartink.scheduler.get_job("squareoff_2")
    assert str(job.trigger.timezone) == "Asia/Kolkata"
    assert [str(f) for f in job.trigger.fields if f.name in ("hour", "minute")] == ["14", "45"]


def test_restore_skips_positional_and_untimed_strategies(chartink):
    chartink.rows.update(
        {1: _strategy(1, intraday=False), 2: _strategy(2, squareoff=None), 3: _strategy(3)}
    )
    assert chartink.restore_squareoff_jobs() == 1
    assert chartink.scheduler.get_job("squareoff_1") is None
    assert chartink.scheduler.get_job("squareoff_2") is None


def test_restore_twice_leaves_one_job_and_a_bad_time_does_not_stop_the_rest(chartink):
    chartink.rows.update({1: _strategy(1), 2: _strategy(2, squareoff="bogus"), 3: _strategy(3)})
    chartink.restore_squareoff_jobs()
    chartink.restore_squareoff_jobs()
    ids = sorted(j.id for j in chartink.scheduler.get_jobs() if j.id.startswith("squareoff_"))
    assert ids == ["squareoff_1", "squareoff_3"]


def test_restore_with_no_strategies_is_zero(chartink):
    assert chartink.restore_squareoff_jobs() == 0


# --- strategy synthesis: the search hands the (single) server thread back while it works ---------------------


def _toy_search():
    import numpy as np

    from services.strategy_synthesis import search
    from services.strategy_synthesis.payoff import SynthesizedLeg

    templates = [
        SynthesizedLeg(strike=20000.0 + 50 * i, option_type=ot, side=side, premium=50.0 + i)
        for i in range(8)
        for ot in ("CE", "PE")
        for side in ("BUY", "SELL")
    ]
    prices = np.linspace(19000.0, 21000.0, 40)
    ctx = search._ScoringContext(
        lot_size=75, shape_weight=1.0, profit_weight=0.0, loss_weight=0.0, win_prob_weight=0.0,
        leg_count_penalty=0.0, profit_normalization=1.0, spot=None, iv=None, years=None,
    )
    return search, templates, prices, np.zeros_like(prices), ctx


def test_exhaustive_search_yields_to_the_hub_regularly(monkeypatch):
    search, templates, prices, target, ctx = _toy_search()
    yields = []
    monkeypatch.setattr(search.time, "sleep", yields.append)
    monkeypatch.setattr(search, "_scored", 0)

    out = search._exhaustive(templates, 3, prices, target, ctx)

    assert len(out) == 4960  # C(32, 3)
    assert yields and set(yields) == {0}
    assert len(yields) == 4960 // search._YIELD_EVERY


def test_greedy_search_yields_too(monkeypatch):
    search, templates, prices, target, ctx = _toy_search()
    yields = []
    monkeypatch.setattr(search.time, "sleep", yields.append)
    monkeypatch.setattr(search, "_scored", 0)
    monkeypatch.setattr(search, "_YIELD_EVERY", 16)

    search._greedy(templates, 3, prices, target, ctx)

    assert len(yields) >= 1


def test_yielding_does_not_change_the_result(monkeypatch):
    search, templates, prices, target, ctx = _toy_search()
    monkeypatch.setattr(search.time, "sleep", lambda _s: None)
    plain = search._exhaustive(templates, 2, prices, target, ctx)
    monkeypatch.setattr(search, "_YIELD_EVERY", 1)
    eager = search._exhaustive(templates, 2, prices, target, ctx)
    assert [round(r.score, 9) for r in plain] == [round(r.score, 9) for r in eager]


# --- flow scheduler: daily/weekly workflow times are IST whatever the machine's zone is ------------------------


@pytest.fixture
def flow_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler

    from services.flow_scheduler_service import FlowScheduler

    svc = FlowScheduler()
    svc._scheduler = BackgroundScheduler(timezone="UTC")
    svc._scheduler.start(paused=True)
    yield svc
    svc._scheduler.shutdown(wait=False)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"schedule_type": "daily", "time_str": "09:20"},
        {"schedule_type": "weekly", "time_str": "09:20", "days": [0, 4]},
        {"schedule_type": "weekly", "time_str": "15:25", "days": [2]},
    ],
)
def test_cron_workflow_triggers_carry_ist(flow_scheduler, kwargs):
    flow_scheduler.add_workflow_job(workflow_id=7, **kwargs)
    trigger = flow_scheduler.scheduler.get_job("flow_workflow_7").trigger
    assert str(trigger.timezone) == "Asia/Kolkata"


# --- mcp http transport: a placeholder key must not stick --------------------------------------------------------


def test_http_transport_retries_while_no_api_key_exists(monkeypatch):
    import blueprints.mcp_http as mh
    from utils import mcp_tool_registry as reg

    bound = []
    fake_module = SimpleNamespace(init_for_http=lambda key, host: bound.append(key))
    monkeypatch.setattr(reg, "_load_mcpserver_module", lambda: fake_module)
    monkeypatch.setattr(reg, "audit_registry", lambda: None)
    keys = iter([None, None, "real-key", "later-key"])
    import database.auth_db as auth_db

    monkeypatch.setattr(auth_db, "get_first_available_api_key", lambda: next(keys))
    monkeypatch.setattr(mh, "_initialized", False)

    mh.init_http_transport()
    mh.init_http_transport()
    assert bound == ["<not-configured>", "<not-configured>"] and mh._initialized is False
    mh.init_http_transport()
    assert bound[-1] == "real-key" and mh._initialized is True
    mh.init_http_transport()  # idempotent once a real key is bound
    assert bound[-1] == "real-key"


# --- simulator master-contract rebuild: a failed download is reported as failed ----------------------------------


def _rebuild(monkeypatch, *, result, status, raises=False):
    import database.master_contract_status_db as mcs
    import utils.auth_utils as au
    from broker.stock_simulator.api import _mc_rebuild

    def download(_broker):
        if raises:
            raise RuntimeError("boom")
        return result

    monkeypatch.setattr(au, "async_master_contract_download", download)
    monkeypatch.setattr(mcs, "get_status", lambda _b: {"status": status})
    return _mc_rebuild.trigger_mc_rebuild_if_date_changed("2024-01-01", "2024-01-02")


def test_rebuild_reports_completed_on_success(monkeypatch):
    assert _rebuild(monkeypatch, result=None, status="success") == "completed"


def test_rebuild_reports_failed_when_the_status_row_says_error(monkeypatch):
    assert _rebuild(monkeypatch, result=None, status="error") == "failed"


def test_rebuild_reports_failed_on_an_error_dict(monkeypatch):
    assert _rebuild(monkeypatch, result={"status": "error"}, status="success") == "failed"


def test_rebuild_reports_failed_when_the_download_crashes(monkeypatch):
    assert _rebuild(monkeypatch, result=None, status="success", raises=True) == "failed"


def test_rebuild_is_skipped_when_the_date_did_not_change():
    from broker.stock_simulator.api import _mc_rebuild

    assert _mc_rebuild.trigger_mc_rebuild_if_date_changed("2024-01-01", "2024-01-01") is None


# --- python strategy configs: an unreadable file is kept, not overwritten --------------------------------------


def test_corrupt_strategy_config_is_backed_up_before_it_can_be_overwritten(tmp_path, monkeypatch):
    import blueprints.python_strategy as ps

    cfg = tmp_path / "strategy_configs.json"
    cfg.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(ps, "CONFIG_FILE", cfg)
    ps.load_configs()
    assert ps.STRATEGY_CONFIGS == {}
    backups = list(tmp_path.glob("strategy_configs.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "{not json"


def test_good_strategy_config_leaves_no_backup(tmp_path, monkeypatch):
    import blueprints.python_strategy as ps

    cfg = tmp_path / "strategy_configs.json"
    cfg.write_text(json.dumps({"a": {"exchange": "NSE"}}), encoding="utf-8")
    monkeypatch.setattr(ps, "CONFIG_FILE", cfg)
    ps.load_configs()
    assert list(ps.STRATEGY_CONFIGS) == ["a"]
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_missing_strategy_config_is_not_an_error(tmp_path, monkeypatch):
    import blueprints.python_strategy as ps

    monkeypatch.setattr(ps, "CONFIG_FILE", tmp_path / "absent.json")
    ps.load_configs()
    assert not list(tmp_path.iterdir())
