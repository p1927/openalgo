"""Bounded per-job log buffer feeding the Scheduler tab's live-log-tail.

Mirrors vibetrading-agent's ``scheduled_research/run_log_buffer.py`` and
stock_simulator's ``service/log_buffer.py`` (same bounded-deque-plus-
monotonic-seq design — a caller polling with ``since_seq`` must see the
right tail even after ``maxlen`` has evicted older entries, which a
deque-index would not). Independent module, not shared — three separate
processes, no runtime dependency on each other beyond HTTP.

Keyed by the exact APScheduler job id each scheduler already uses
(``f"flow_workflow_{workflow_id}"``, ``f"historify_schedule_{schedule_id}"``)
— the same string that appears after ``"C:<source>:"`` in a scheduler-registry
entry's ``id``, so the aggregation layer's URL-stamping needs no extra
lookup table.
"""

from __future__ import annotations

import threading
import time
from collections import deque

_MAX_LOGS_PER_JOB = 500

_LOCK = threading.Lock()
_BUFFERS: dict[str, deque[dict]] = {}
_SEQ_COUNTERS: dict[str, int] = {}


def append_log(job_id: str, message: str) -> None:
    """Append one log line for ``job_id``, evicting the oldest past 500."""
    with _LOCK:
        seq = _SEQ_COUNTERS.get(job_id, 0) + 1
        _SEQ_COUNTERS[job_id] = seq
        buf = _BUFFERS.setdefault(job_id, deque(maxlen=_MAX_LOGS_PER_JOB))
        buf.append({"seq": seq, "at": int(time.time() * 1000), "message": message})


def get_logs_since(job_id: str, since_seq: int = 0) -> list[dict]:
    """Return log entries for ``job_id`` with ``seq > since_seq``, oldest first."""
    with _LOCK:
        buf = _BUFFERS.get(job_id)
        if buf is None:
            return []
        return [entry for entry in buf if entry["seq"] > since_seq]
