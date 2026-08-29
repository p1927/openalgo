"""Bounded per-job log buffer (Mechanism C's live-log-tail) plus the
instrumentation added to ``execute_workflow_scheduled`` (Flow) and
``execute_schedule`` (Historify) — see
.claude/backlog/items/2026-08-29-unified-scheduler-registry.md.
"""

from __future__ import annotations

from unittest import mock

import pytest

from services import scheduler_run_log_buffer as buf


@pytest.fixture(autouse=True)
def _isolate_buffers():
    yield
    buf._BUFFERS.clear()
    buf._SEQ_COUNTERS.clear()


def test_append_and_get_all():
    buf.append_log("flow_workflow_1", "hello")
    buf.append_log("flow_workflow_1", "world")
    entries = buf.get_logs_since("flow_workflow_1")
    assert [e["message"] for e in entries] == ["hello", "world"]
    assert [e["seq"] for e in entries] == [1, 2]


def test_get_logs_since_only_returns_new_entries():
    buf.append_log("flow_workflow_1", "a")
    first = buf.get_logs_since("flow_workflow_1")
    buf.append_log("flow_workflow_1", "b")
    second = buf.get_logs_since("flow_workflow_1", since_seq=first[-1]["seq"])
    assert [e["message"] for e in second] == ["b"]


def test_unknown_job_returns_empty():
    assert buf.get_logs_since("nonexistent") == []


def test_eviction_past_maxlen_does_not_break_since_seq_filtering():
    for i in range(buf._MAX_LOGS_PER_JOB + 50):
        buf.append_log("flow_workflow_1", f"line-{i}")
    all_entries = buf.get_logs_since("flow_workflow_1")
    assert len(all_entries) == buf._MAX_LOGS_PER_JOB
    assert all_entries[0]["seq"] == 51
    assert buf.get_logs_since("flow_workflow_1", since_seq=0) == all_entries


# --- execute_workflow_scheduled instrumentation ----------------------------


def test_execute_workflow_scheduled_logs_missing_api_key():
    from services.flow_scheduler_service import execute_workflow_scheduled

    execute_workflow_scheduled(5, api_key=None)

    messages = [e["message"] for e in buf.get_logs_since("flow_workflow_5")]
    assert messages == ["starting", "failed: no API key available"]


def test_execute_workflow_scheduled_logs_completion_on_success():
    from services.flow_scheduler_service import execute_workflow_scheduled

    with mock.patch("database.flow_db.get_workflow", return_value=None), mock.patch(
        "services.flow_executor_service.execute_workflow",
        return_value={"status": "success"},
    ), mock.patch("utils.db_sessions.remove_all_scoped_sessions"):
        execute_workflow_scheduled(7, api_key="key")

    messages = [e["message"] for e in buf.get_logs_since("flow_workflow_7")]
    assert messages == ["starting", "completed: success"]


def test_execute_workflow_scheduled_logs_failure_on_exception():
    from services.flow_scheduler_service import execute_workflow_scheduled

    with mock.patch("database.flow_db.get_workflow", return_value=None), mock.patch(
        "services.flow_executor_service.execute_workflow",
        side_effect=RuntimeError("boom"),
    ), mock.patch("utils.db_sessions.remove_all_scoped_sessions"):
        execute_workflow_scheduled(9, api_key="key")

    messages = [e["message"] for e in buf.get_logs_since("flow_workflow_9")]
    assert messages == ["starting", "failed: boom"]


# --- execute_schedule instrumentation --------------------------------------


def test_execute_schedule_logs_schedule_not_found():
    from services.historify_scheduler_service import execute_schedule

    with mock.patch("database.historify_db.get_schedule", return_value=None), mock.patch(
        "utils.db_sessions.remove_all_scoped_sessions"
    ):
        execute_schedule("sched-1")

    messages = [e["message"] for e in buf.get_logs_since("historify_schedule_sched-1")]
    assert messages == ["starting", "failed: schedule not found"]


def test_execute_schedule_logs_completion_with_the_scheduler_job_id_not_the_download_job_id():
    """Regression: the function's own `job_id` local gets reassigned to the
    *download* job's id inside the success branch — the log buffer must stay
    keyed by the *scheduler's* job id (`historify_schedule_<schedule_id>`,
    the same string the registry entry's `id` embeds), not get silently
    redirected to the download job's id."""
    from services.historify_scheduler_service import execute_schedule

    schedule = {
        "id": "sched-2",
        "is_enabled": True,
        "is_paused": False,
        "lookback_days": 1,
        "data_interval": "D",
    }
    with mock.patch("database.historify_db.get_schedule", return_value=schedule), mock.patch(
        "database.historify_db.get_watchlist",
        return_value=[{"symbol": "INFY", "exchange": "NSE"}],
    ), mock.patch(
        "database.historify_db.create_schedule_execution", return_value=None
    ), mock.patch(
        "database.historify_db.update_schedule"
    ), mock.patch(
        "database.historify_db.increment_schedule_run_counts"
    ), mock.patch(
        "services.historify_service.create_and_start_job",
        return_value=(True, {"job_id": "download-job-99"}, 200),
    ), mock.patch(
        "services.historify_scheduler_service.get_historify_scheduler"
    ) as fake_get_scheduler, mock.patch(
        "utils.db_sessions.remove_all_scoped_sessions"
    ):
        fake_get_scheduler.return_value = mock.Mock(api_key="key", socketio=None)
        execute_schedule("sched-2", api_key="key")

    # Nothing was ever written under the download job's id.
    assert buf.get_logs_since("download-job-99") == []
    messages = [e["message"] for e in buf.get_logs_since("historify_schedule_sched-2")]
    assert messages == ["starting", "completed: download job download-job-99 started (1 symbols)"]
