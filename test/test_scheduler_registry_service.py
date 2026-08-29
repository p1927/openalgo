"""Aggregated read/control surface over openalgo's five APScheduler instances.

Covers: apikey gating, per-source graceful degradation (one broken/uninitialized
scheduler never breaks the whole listing), the source-prefixed id/section shape
the frontend relies on, and pause/resume dispatch.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from services import scheduler_registry_service as svc


def _job(job_id, name=None, next_run_time=None, trigger="interval[0:01:00]"):
    return SimpleNamespace(id=job_id, name=name, next_run_time=next_run_time, trigger=trigger)


def _fake_scheduler(jobs):
    scheduler = mock.Mock()
    scheduler.get_jobs.return_value = jobs
    return scheduler


@pytest.fixture(autouse=True)
def _valid_api_key():
    with mock.patch.object(svc, "get_auth_token_broker", return_value=("token", "broker")):
        yield


def test_invalid_api_key_rejected():
    with mock.patch.object(svc, "get_auth_token_broker", return_value=(None, None)):
        ok, body, status = svc.list_scheduler_registry("bad-key")
    assert ok is False
    assert status == 403


def test_missing_api_key_rejected():
    ok, body, status = svc.list_scheduler_registry(None)
    assert ok is False
    assert status == 403


def test_lists_jobs_from_every_source():
    scheduler = _fake_scheduler([_job("wf_1", next_run_time=datetime(2026, 1, 1))])
    with mock.patch.object(svc, "_get_scheduler", return_value=scheduler):
        ok, body, status = svc.list_scheduler_registry("key")
    assert ok is True
    assert status == 200
    entries = body["data"]["entries"]
    # One source's scheduler is patched in for all five VALID_SOURCES, so
    # every source contributes its one job.
    assert len(entries) == len(svc.VALID_SOURCES)
    for entry in entries:
        assert entry["source"] == "openalgo"
        assert entry["id"].startswith("C:")
        assert entry["enabled"] is True
        assert entry["controls"] == {
            "pause": True,
            "resume": True,
            "cancel": False,
            "delete": False,
            "trigger_now": False,
        }


def test_id_and_section_embed_the_source():
    with mock.patch.object(svc, "_get_scheduler") as get_scheduler:
        def pick(source):
            if source == "flow":
                return _fake_scheduler([_job("wf_9")])
            return None

        get_scheduler.side_effect = pick
        ok, body, _status = svc.list_scheduler_registry("key")
    assert ok is True
    entries = body["data"]["entries"]
    assert entries == [
        {
            "id": "C:flow:wf_9",
            "source": "openalgo",
            "section": "flow",
            "label": "wf_9",
            "description": None,
            "schedule_kind": "apscheduler_trigger",
            "schedule_display": "interval[0:01:00]",
            "enabled": False,
            "status": "idle",
            "cancel_requested": False,
            "next_run_at": None,
            "last_run_at": None,
            "last_error": None,
            "auto_paused_reason": None,
            "supports_live_log": False,
            "live_log_stream_url": None,
            "controls": {
                "pause": True,
                "resume": True,
                "cancel": False,
                "delete": False,
                "trigger_now": False,
            },
        }
    ]


def test_uninitialized_scheduler_contributes_no_entries_not_an_error():
    with mock.patch.object(svc, "_get_scheduler", return_value=None):
        ok, body, status = svc.list_scheduler_registry("key")
    assert ok is True
    assert status == 200
    assert body["data"]["entries"] == []


def test_one_broken_source_does_not_break_the_listing():
    def pick(source):
        if source == "strategy":
            raise RuntimeError("boom")
        if source == "flow":
            return _fake_scheduler([_job("wf_1")])
        return None

    with mock.patch.object(svc, "_get_scheduler", side_effect=pick):
        ok, body, status = svc.list_scheduler_registry("key")
    assert ok is True
    assert status == 200
    assert [e["id"] for e in body["data"]["entries"]] == ["C:flow:wf_1"]


def test_pause_dispatches_to_the_right_scheduler():
    scheduler = _fake_scheduler([])
    with mock.patch.object(svc, "_get_scheduler", return_value=scheduler):
        ok, body, status = svc.pause_scheduler_job("key", "flow", "wf_1")
    assert ok is True
    assert status == 200
    scheduler.pause_job.assert_called_once_with("wf_1")


def test_resume_dispatches_to_the_right_scheduler():
    scheduler = _fake_scheduler([])
    with mock.patch.object(svc, "_get_scheduler", return_value=scheduler):
        ok, body, status = svc.resume_scheduler_job("key", "historify", "sched_2")
    assert ok is True
    assert status == 200
    scheduler.resume_job.assert_called_once_with("sched_2")


def test_pause_unknown_source_rejected():
    ok, body, status = svc.pause_scheduler_job("key", "not_a_source", "job")
    assert ok is False
    assert status == 400


def test_pause_uninitialized_scheduler_rejected():
    with mock.patch.object(svc, "_get_scheduler", return_value=None):
        ok, body, status = svc.pause_scheduler_job("key", "flow", "wf_1")
    assert ok is False
    assert status == 400


def test_pause_invalid_api_key_rejected():
    with mock.patch.object(svc, "get_auth_token_broker", return_value=(None, None)):
        ok, body, status = svc.pause_scheduler_job("bad-key", "flow", "wf_1")
    assert ok is False
    assert status == 403


def test_pause_apscheduler_error_surfaces_as_failure():
    scheduler = mock.Mock()
    scheduler.pause_job.side_effect = Exception("no such job")
    with mock.patch.object(svc, "_get_scheduler", return_value=scheduler):
        ok, body, status = svc.pause_scheduler_job("key", "chartink", "missing")
    assert ok is False
    assert status == 400
