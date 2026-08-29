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

from typing import Any

from database.auth_db import get_auth_token_broker
from utils.logging import get_logger

logger = get_logger(__name__)

VALID_SOURCES = ("flow", "historify", "strategy", "chartink", "python_strategy")


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
    return {
        "id": f"C:{source}:{job.id}",
        "source": "openalgo",
        "section": source,
        "label": job.name or job.id,
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
