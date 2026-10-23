"""sync_all_jobs at boot: a database read that fails must retry, then fail loudly (it used to install
nothing, silently, leaving every strategy without its exit-time square-off). Reuses the fixtures of
test_strategy_module_scheduler.py (paused scheduler, purged strategies)."""

import pytest

import restx_api  # noqa: F401  (import order: see test_strategy_module_order_dispatch.py)
from database import strategy_module_db as store
from services.strategy_module import scheduler as sched
from test_strategy_module_scheduler import _job, _make, clean_slate  # noqa: F401


class _FlakyQuery:
    """`db_session.query(...)` that raises ``failures`` times, then answers from the real session."""

    def __init__(self, failures):
        self.failures = failures
        self.calls = 0
        self.real = store.db_session.query

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("database is locked")
        return self.real(*args, **kwargs)


def test_retries_a_read_that_lost_the_lock_race(monkeypatch):
    sid = _make()
    flaky = _FlakyQuery(failures=2)
    monkeypatch.setattr(sched.time, "sleep", lambda _s: None)
    monkeypatch.setattr(store.db_session, "query", flaky)

    sched.sync_all_jobs()

    assert flaky.calls >= 3  # two failed reads, then the read plus the per-strategy queries
    assert _job(sched.start_job_id(sid)) is not None


def test_raises_when_every_attempt_fails_and_drops_nothing(monkeypatch):
    sid = _make()
    sched.sync_strategy_jobs(sid)
    flaky = _FlakyQuery(failures=99)
    monkeypatch.setattr(sched.time, "sleep", lambda _s: None)
    monkeypatch.setattr(store.db_session, "query", flaky)

    with pytest.raises(RuntimeError, match="NOT rebuilt"):
        sched.sync_all_jobs()

    assert flaky.calls == sched.SYNC_READ_ATTEMPTS
    # An unreadable table is not "every strategy is gone": the job already installed stays.
    assert _job(sched.start_job_id(sid)) is not None


def test_reads_once_when_the_first_read_works(monkeypatch):
    _make()
    flaky = _FlakyQuery(failures=0)
    slept = []
    monkeypatch.setattr(sched.time, "sleep", slept.append)
    monkeypatch.setattr(store.db_session, "query", flaky)

    sched.sync_all_jobs()

    assert slept == []  # no failed read, so no retry pause
