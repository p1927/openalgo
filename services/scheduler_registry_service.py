"""Read/control surface aggregating openalgo's independent APScheduler instances.

openalgo has no single scheduler: Flow and Historify each own a persistent
``BackgroundScheduler`` behind a service singleton
(``flow_scheduler_service.py``, ``historify_scheduler_service.py``), while
Strategy, Chartink, and the Python Strategy Host each keep a bare
module-level ``BackgroundScheduler`` with no admin surface. This module gives
Trade's unified scheduler registry (vibetrading-agent's ``/scheduler-registry``
aggregation route, via ``OpenAlgoClient``) one place to list and
pause/resume across all five, using each scheduler's already-working APScheduler
job store — no new persistence, no change to any ``add_job``/``remove_job``
call site.

Enforcer's OS crontab is a separate product and is never surfaced here.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

from database.auth_db import get_auth_token_broker
from utils.logging import get_logger

logger = get_logger(__name__)

_STREAM_POLL_SECONDS = 0.5
_STREAM_HEARTBEAT_SECONDS = 15.0

VALID_SOURCES = ("flow", "historify", "strategy", "chartink", "python_strategy")

# Sources whose scheduled entry points have been instrumented with
# scheduler_run_log_buffer.append_log calls and have a live-log-tail SSE
# route (restx_api/scheduler_registry.py's `.../stream` endpoint).
# strategy/chartink's squareoff_positions and python_strategy's five job
# functions (scheduled_start_strategy, scheduled_stop_strategy,
# daily_trading_day_check, market_hours_enforcer, cleanup_dead_processes)
# each log start/skip/complete/fail at their own APScheduler job id, even
# though (unlike Flow/Historify's one async dispatch function) the actual
# order placement/subprocess work happens downstream of that entry point
# (order queue drained by a separate worker thread; subprocess lifecycle
# funneled through start_strategy_process/stop_strategy_process) — the log
# line describes what the scheduled job itself did, not full downstream
# execution detail.
_LIVE_LOG_SOURCES = frozenset({"flow", "historify", "strategy", "chartink", "python_strategy"})


def _validate_api_key(api_key: str | None) -> bool:
    if not api_key:
        return False
    auth_token, _broker = get_auth_token_broker(api_key)
    return auth_token is not None


def _get_scheduler(source: str):
    """Return the live ``BackgroundScheduler`` for a source, or ``None`` if unavailable."""
    if source == "flow":
        from services.flow_scheduler_service import get_flow_scheduler

        try:
            return get_flow_scheduler().scheduler
        except RuntimeError:
            return None
    if source == "historify":
        from services.historify_scheduler_service import get_historify_scheduler

        try:
            return get_historify_scheduler().scheduler
        except RuntimeError:
            return None
    if source == "strategy":
        from blueprints.strategy import scheduler

        return scheduler
    if source == "chartink":
        from blueprints.chartink import scheduler

        return scheduler
    if source == "python_strategy":
        from blueprints.python_strategy import SCHEDULER

        return SCHEDULER
    raise ValueError(f"Unknown scheduler source: {source}")


def _job_to_entry(source: str, job: Any) -> dict[str, Any]:
    # next_run_at is epoch milliseconds, matching stock_simulator's DTO
    # convention (scheduler_introspection.list_recorder_categories) —
    # the frontend's SchedulerRegistryEntry type expects `number | null`,
    # not an ISO string, across every source.
    next_run_at = int(job.next_run_time.timestamp() * 1000) if job.next_run_time else None
    # job.id is the caller-assigned identifier (e.g. "reap_dead_strategies",
    # "start_<strategy>") and is consistently the more readable label; job.name
    # falls back to the scheduled callable's __qualname__ when APScheduler
    # isn't given an explicit name, which for a job added via a closure (seen
    # live: python_strategy's per-strategy start/stop jobs) renders as
    # "schedule_strategy.<locals>.<lambda>" — never something to show a user.
    return {
        "id": f"C:{source}:{job.id}",
        "source": "openalgo",
        "section": source,
        "label": job.id,
        "description": None,
        "schedule_kind": "apscheduler_trigger",
        "schedule_display": str(job.trigger),
        "enabled": job.next_run_time is not None,
        "status": "idle",
        "cancel_requested": False,
        "next_run_at": next_run_at,
        "last_run_at": None,
        "last_error": None,
        "auto_paused_reason": None,
        # live_log_stream_url is stamped in by the cross-service caller
        # (vibetrading-agent's `_openalgo_entries`, via `OpenAlgoClient.log_stream_url`),
        # same pattern stock_simulator's DTO uses — this process doesn't
        # embed its own apikey into a URL from inside a service module.
        "supports_live_log": source in _LIVE_LOG_SOURCES,
        "live_log_stream_url": None,
        "controls": {
            "pause": True,
            "resume": True,
            "cancel": False,
            "delete": False,
            "trigger_now": False,
        },
    }


def list_scheduler_registry(api_key: str | None) -> tuple[bool, dict[str, Any], int]:
    """List every job across openalgo's five scheduler instances.

    A source whose scheduler has not been initialized (e.g. Flow/Historify
    before their first use) contributes no entries rather than erroring —
    it is not "unreachable", it simply has nothing running yet.
    """
    if not _validate_api_key(api_key):
        return False, {"status": "error", "message": "Invalid openalgo apikey"}, 403

    entries: list[dict[str, Any]] = []
    for source in VALID_SOURCES:
        try:
            scheduler = _get_scheduler(source)
        except Exception:
            logger.exception("Failed to resolve %s scheduler for registry listing", source)
            continue
        if scheduler is None:
            continue
        try:
            jobs = scheduler.get_jobs()
        except Exception:
            logger.exception("Failed to list jobs from %s scheduler", source)
            continue
        entries.extend(_job_to_entry(source, job) for job in jobs)

    return True, {"status": "success", "data": {"entries": entries}}, 200


def _set_job_paused(source: str, job_id: str, *, paused: bool) -> tuple[bool, str]:
    if source not in VALID_SOURCES:
        return False, f"Unknown scheduler source: {source}"
    try:
        scheduler = _get_scheduler(source)
    except Exception as exc:
        return False, str(exc)
    if scheduler is None:
        return False, f"{source} scheduler is not running"
    try:
        if paused:
            scheduler.pause_job(job_id)
        else:
            scheduler.resume_job(job_id)
        return True, "ok"
    except Exception as exc:
        logger.exception("Failed to %s job %s on %s scheduler", "pause" if paused else "resume", job_id, source)
        return False, str(exc)


def pause_scheduler_job(
    api_key: str | None, source: str, job_id: str
) -> tuple[bool, dict[str, Any], int]:
    if not _validate_api_key(api_key):
        return False, {"status": "error", "message": "Invalid openalgo apikey"}, 403
    ok, message = _set_job_paused(source, job_id, paused=True)
    if not ok:
        return False, {"status": "error", "message": message}, 400
    return True, {"status": "success", "message": "Job paused"}, 200


def resume_scheduler_job(
    api_key: str | None, source: str, job_id: str
) -> tuple[bool, dict[str, Any], int]:
    if not _validate_api_key(api_key):
        return False, {"status": "error", "message": "Invalid openalgo apikey"}, 403
    ok, message = _set_job_paused(source, job_id, paused=False)
    if not ok:
        return False, {"status": "error", "message": message}, 400
    return True, {"status": "success", "message": "Job resumed"}, 200


def validate_stream_access(api_key: str | None, source: str) -> tuple[bool, str]:
    """Gate for ``GET .../<source>/<job_id>/stream`` before any streaming starts.

    Kept separate from the POST-endpoint auth check above only because the
    apikey arrives as a query param here (an ``EventSource`` can't send a
    JSON body or a header) — same validation, same
    ``database.auth_db.get_auth_token_broker`` call, no new auth mechanism.
    """
    if not _validate_api_key(api_key):
        return False, "Invalid openalgo apikey"
    if source not in _LIVE_LOG_SOURCES:
        return False, f"Live-log-tail not available for source: {source!r}"
    return True, ""


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def stream_scheduler_run_log(job_id: str) -> Iterator[str]:
    """Replay ``job_id``'s buffered log lines, then poll for new ones forever.

    Same replay-then-poll shape and ``event: log`` / ``{"seq","at","message"}``
    frame format as vibetrading-agent's and stock_simulator's live-log-tail
    routes, so the frontend's ``LiveLogTail.tsx`` needs no source-specific
    branching. A plain blocking generator (``time.sleep``, not ``asyncio``)
    per this codebase's own eventlet-compatibility rule — Flask/eventlet
    streams a generator response natively, and a client disconnect surfaces
    as a write failure on the next yield rather than needing an explicit
    ``is_disconnected()`` poll the way FastAPI's routes use.
    """
    from services.scheduler_run_log_buffer import get_logs_since

    last_seq = 0
    last_emit = time.monotonic()
    while True:
        for entry in get_logs_since(job_id, last_seq):
            yield _sse_frame("log", entry)
            last_seq = entry["seq"]
            last_emit = time.monotonic()
        if time.monotonic() - last_emit >= _STREAM_HEARTBEAT_SECONDS:
            yield ": keepalive\n\n"
            last_emit = time.monotonic()
        time.sleep(_STREAM_POLL_SECONDS)
