"""Live-log-tail instrumentation for strategy/chartink squareoff and
python_strategy's five scheduled job functions — the remaining scope of
.claude/backlog/items/2026-08-29-unified-scheduler-registry.md.

Unlike Flow/Historify's single async dispatch function, these sources have no
one callback every scheduled invocation funnels through end-to-end: strategy/
chartink's squareoff only queues orders (the actual placement happens later in
a decoupled worker thread with no job id in scope), and python_strategy's five
job functions include two batch jobs (market_hours_enforcer,
daily_trading_day_check) that can touch many strategy_ids per fire. Each
function logs what it itself did (start/skip/complete/fail), at its own
APScheduler job id, matching the coarse start/complete/fail granularity used
for Flow/Historify.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from services import scheduler_run_log_buffer as buf


@pytest.fixture(autouse=True)
def _isolate_buffers():
    yield
    buf._BUFFERS.clear()
    buf._SEQ_COUNTERS.clear()


# --- strategy.py's squareoff_positions --------------------------------------


def _strategy_obj(user_id="u1", name="My Strategy", is_intraday=True):
    return SimpleNamespace(user_id=user_id, name=name, is_intraday=is_intraday)


def _mapping(symbol="INFY", exchange="NSE", product_type="MIS"):
    return SimpleNamespace(symbol=symbol, exchange=exchange, product_type=product_type)


def test_strategy_squareoff_logs_no_api_key():
    from blueprints import strategy as strategy_bp

    with mock.patch.object(strategy_bp, "get_strategy", return_value=_strategy_obj()), \
        mock.patch.object(strategy_bp, "get_api_key_for_tradingview", return_value=None):
        strategy_bp.squareoff_positions("s1")

    messages = [e["message"] for e in buf.get_logs_since("squareoff_s1")]
    assert messages == [
        "starting squareoff for strategy s1",
        "failed: no API key for strategy s1",
    ]


def test_strategy_squareoff_logs_skip_not_intraday():
    from blueprints import strategy as strategy_bp

    with mock.patch.object(
        strategy_bp, "get_strategy", return_value=_strategy_obj(is_intraday=False)
    ):
        strategy_bp.squareoff_positions("s1")

    messages = [e["message"] for e in buf.get_logs_since("squareoff_s1")]
    assert messages == [
        "starting squareoff for strategy s1",
        "skipped: strategy not found or not intraday for strategy s1",
    ]


def test_strategy_squareoff_logs_completion_on_success():
    from blueprints import strategy as strategy_bp

    with mock.patch.object(strategy_bp, "get_strategy", return_value=_strategy_obj()), \
        mock.patch.object(strategy_bp, "get_api_key_for_tradingview", return_value="key"), \
        mock.patch.object(
            strategy_bp, "get_symbol_mappings", return_value=[_mapping(), _mapping("TCS")]
        ), \
        mock.patch.object(strategy_bp, "queue_order") as queue_order:
        strategy_bp.squareoff_positions("s1")

    assert queue_order.call_count == 2
    messages = [e["message"] for e in buf.get_logs_since("squareoff_s1")]
    assert messages == [
        "starting squareoff for strategy s1",
        "squareoff completed: 2 order(s) queued",
    ]


def test_strategy_squareoff_logs_failure_on_exception():
    from blueprints import strategy as strategy_bp

    with mock.patch.object(
        strategy_bp, "get_strategy", side_effect=RuntimeError("db down")
    ):
        strategy_bp.squareoff_positions("s1")

    messages = [e["message"] for e in buf.get_logs_since("squareoff_s1")]
    assert messages == ["starting squareoff for strategy s1", "failed: db down"]


# --- chartink.py's squareoff_positions --------------------------------------


def _chartink_mapping(chartink_symbol="INFY", exchange="NSE", product_type="MIS"):
    return SimpleNamespace(
        chartink_symbol=chartink_symbol, exchange=exchange, product_type=product_type
    )


def test_chartink_squareoff_logs_completion_on_success():
    from blueprints import chartink as chartink_bp

    with mock.patch.object(chartink_bp, "get_strategy", return_value=_strategy_obj()), \
        mock.patch.object(chartink_bp, "get_api_key_for_tradingview", return_value="key"), \
        mock.patch.object(
            chartink_bp, "get_symbol_mappings", return_value=[_chartink_mapping()]
        ), \
        mock.patch.object(chartink_bp, "queue_order") as queue_order:
        chartink_bp.squareoff_positions("c1")

    assert queue_order.call_count == 1
    messages = [e["message"] for e in buf.get_logs_since("squareoff_c1")]
    assert messages == [
        "starting squareoff for strategy c1",
        "squareoff completed: 1 order(s) queued",
    ]


def test_chartink_squareoff_logs_skip_not_intraday():
    from blueprints import chartink as chartink_bp

    with mock.patch.object(
        chartink_bp, "get_strategy", return_value=_strategy_obj(is_intraday=False)
    ):
        chartink_bp.squareoff_positions("c1")

    messages = [e["message"] for e in buf.get_logs_since("squareoff_c1")]
    assert messages == [
        "starting squareoff for strategy c1",
        "skipped: strategy not found or not intraday for strategy c1",
    ]


def test_chartink_squareoff_logs_no_api_key():
    from blueprints import chartink as chartink_bp

    with mock.patch.object(chartink_bp, "get_strategy", return_value=_strategy_obj()), \
        mock.patch.object(chartink_bp, "get_api_key_for_tradingview", return_value=None):
        chartink_bp.squareoff_positions("c1")

    messages = [e["message"] for e in buf.get_logs_since("squareoff_c1")]
    assert messages == [
        "starting squareoff for strategy c1",
        "failed: no API key for strategy c1",
    ]


# --- python_strategy.py's five scheduled job functions ----------------------


def test_scheduled_start_strategy_logs_manually_stopped_skip():
    from blueprints import python_strategy as ps

    with mock.patch.dict(
        ps.STRATEGY_CONFIGS, {"p1": {"manually_stopped": True}}, clear=True
    ):
        ps.scheduled_start_strategy("p1")

    messages = [e["message"] for e in buf.get_logs_since("start_p1")]
    assert messages == [
        "scheduled start fired for strategy p1",
        "skipped: strategy manually stopped",
    ]


def test_scheduled_start_strategy_logs_started_on_success():
    from blueprints import python_strategy as ps

    config = {
        "manually_stopped": False,
        "schedule_days": [],
        "exchange": "NSE",
    }
    with mock.patch.dict(ps.STRATEGY_CONFIGS, {"p1": config}, clear=True), \
        mock.patch.object(ps, "is_trading_day_enforcement_enabled", return_value=False), \
        mock.patch.object(
            ps, "start_strategy_process", return_value=(True, "Strategy started")
        ):
        ps.scheduled_start_strategy("p1")

    messages = [e["message"] for e in buf.get_logs_since("start_p1")]
    assert messages == ["scheduled start fired for strategy p1", "started"]


def test_scheduled_start_strategy_logs_failure():
    from blueprints import python_strategy as ps

    config = {
        "manually_stopped": False,
        "schedule_days": [],
        "exchange": "NSE",
    }
    with mock.patch.dict(ps.STRATEGY_CONFIGS, {"p1": config}, clear=True), \
        mock.patch.object(ps, "is_trading_day_enforcement_enabled", return_value=False), \
        mock.patch.object(
            ps, "start_strategy_process", return_value=(False, "Strategy file not found")
        ):
        ps.scheduled_start_strategy("p1")

    messages = [e["message"] for e in buf.get_logs_since("start_p1")]
    assert messages == [
        "scheduled start fired for strategy p1",
        "failed: Strategy file not found",
    ]


def test_scheduled_stop_strategy_logs_stopped():
    from blueprints import python_strategy as ps

    with mock.patch.object(
        ps, "stop_strategy_process", return_value=(True, "Strategy stopped")
    ):
        ps.scheduled_stop_strategy("p1")

    messages = [e["message"] for e in buf.get_logs_since("stop_p1")]
    assert messages == [
        "scheduled stop triggered for strategy p1",
        "stopped",
    ]


def test_scheduled_stop_strategy_logs_failure():
    from blueprints import python_strategy as ps

    with mock.patch.object(
        ps, "stop_strategy_process", return_value=(False, "Strategy not running")
    ):
        ps.scheduled_stop_strategy("p1")

    messages = [e["message"] for e in buf.get_logs_since("stop_p1")]
    assert messages == [
        "scheduled stop triggered for strategy p1",
        "failed: Strategy not running",
    ]


def test_daily_trading_day_check_logs_stop_and_summary():
    from blueprints import python_strategy as ps

    config = {"is_scheduled": True, "exchange": "NSE"}
    with mock.patch.dict(ps.STRATEGY_CONFIGS, {"p1": config}, clear=True), \
        mock.patch.object(ps, "is_trading_day_enforcement_enabled", return_value=True), \
        mock.patch.object(
            ps,
            "get_market_status",
            return_value={"is_trading": False, "reason": "holiday", "message": "NSE closed"},
        ), \
        mock.patch.object(ps, "_is_strategy_running", return_value=True), \
        mock.patch.object(ps, "stop_strategy_process", return_value=(True, "stopped")), \
        mock.patch.object(ps, "save_configs"):
        ps.daily_trading_day_check()

    messages = [e["message"] for e in buf.get_logs_since("daily_trading_day_check")]
    assert messages == [
        "starting daily trading day check",
        "stopping p1 (NSE) - NSE closed",
        "completed: stopped 1 strategy(ies)",
    ]


def test_daily_trading_day_check_logs_failure():
    from blueprints import python_strategy as ps

    with mock.patch.object(
        ps, "is_trading_day_enforcement_enabled", side_effect=RuntimeError("boom")
    ):
        ps.daily_trading_day_check()

    messages = [e["message"] for e in buf.get_logs_since("daily_trading_day_check")]
    assert messages == ["starting daily trading day check", "failed: boom"]


def test_market_hours_enforcer_logs_stop_and_resume():
    from blueprints import python_strategy as ps

    configs = {
        "p1": {"is_scheduled": True, "exchange": "NSE"},
        "p2": {
            "is_scheduled": True,
            "exchange": "NSE",
            "paused_reason": "holiday",
            "schedule_days": [],
        },
    }

    def market_status(exch):
        return {"is_trading": exch != "NSE"} if False else {"is_trading": True}

    # p1 should stop (closed), p2 should resume (open + previously paused)
    status_by_call = iter(
        [
            {"is_trading": False, "reason": "holiday", "message": "NSE closed"},
            {"is_trading": True},
        ]
    )

    with mock.patch.dict(ps.STRATEGY_CONFIGS, configs, clear=True), \
        mock.patch.object(ps, "is_trading_day_enforcement_enabled", return_value=True), \
        mock.patch.object(ps, "get_market_status", side_effect=lambda exch: next(status_by_call)), \
        mock.patch.object(ps, "_is_strategy_running", side_effect=[True, False]), \
        mock.patch.object(ps, "is_within_schedule_time", return_value=True), \
        mock.patch.object(ps, "stop_strategy_process", return_value=(True, "stopped")), \
        mock.patch.object(ps, "start_strategy_process", return_value=(True, "started")), \
        mock.patch.object(ps, "save_configs"):
        ps.market_hours_enforcer()

    messages_p1 = [e["message"] for e in buf.get_logs_since("market_hours_enforcer")]
    assert "stopping p1 (NSE) - NSE closed" in messages_p1
    assert any(m.startswith("resuming paused strategy p2") for m in messages_p1)


def test_cleanup_dead_processes_logs_reap_and_summary():
    import subprocess

    from blueprints import python_strategy as ps

    dead_process = mock.Mock(spec=subprocess.Popen)
    dead_process.poll.return_value = 1
    running = {"d1": {"process": dead_process, "pid": 123}}

    with mock.patch.dict(ps.RUNNING_STRATEGIES, running, clear=True), \
        mock.patch.dict(ps.STRATEGY_CONFIGS, {"d1": {"is_running": True, "pid": 123}}, clear=True), \
        mock.patch.object(ps, "close_log_handle_safely"), \
        mock.patch.object(ps, "save_configs"):
        ps.cleanup_dead_processes()

    messages = [e["message"] for e in buf.get_logs_since("reap_dead_strategies")]
    assert messages == [
        "strategy d1 process is dead, reaping",
        "cleaned up 1 dead process(es)",
    ]
