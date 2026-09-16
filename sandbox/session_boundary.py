"""Session boundary helpers for sandbox position bookkeeping.

``sandbox_positions.updated_at`` is written by the database clock
(``func.now()``), which on the default SQLite database is UTC
(``CURRENT_TIMESTAMP``). ``SESSION_EXPIRY_TIME`` is a wall-clock time on
the host. Comparisons against position timestamps must resolve the
boundary in the database clock.
"""

from datetime import UTC, datetime, timedelta

import pytz

from utils.logging import get_logger

logger = get_logger(__name__)

# Host session schedule is anchored to IST; a naive caller is treated as
# IST wall-clock so the boundary is never silently read as UTC or system time.
IST = pytz.timezone("Asia/Kolkata")


def as_db_utc(aware_local):
    """Convert a timezone-aware local datetime to the naive UTC the database stores.

    ``created_at`` and ``updated_at`` on the sandbox tables are naive DateTime
    columns written by ``func.now()``, which on SQLite is ``CURRENT_TIMESTAMP``,
    i.e. UTC. A naive IST value compared against them is not rejected, it is
    simply read as UTC, so the comparison silently skews by the offset. That is
    the same mistake twice over in this codebase: the MIS session boundary and
    the T+1 settlement cutoff were each 5.5 hours out. Route every local-to-column
    comparison through here rather than converting by hand at the call site.

    Args:
        aware_local: Timezone-aware datetime in any zone.

    Returns:
        Naive UTC datetime, directly comparable with the columns.
    """
    return aware_local.astimezone(UTC).replace(tzinfo=None)


def _last_session_expiry_boundary(session_expiry_str, now_local):
    """Resolve the most recent session boundary as a timezone-aware local (IST) datetime.

    Args:
        session_expiry_str: Wall-clock boundary from config (e.g. '03:00').
        now_local: Timezone-aware current time in the host's timezone.

    Returns:
        Timezone-aware IST datetime of the most recent session expiry.
    """
    if now_local.tzinfo is None:
        logger.warning(
            "_last_session_expiry_boundary received a naive datetime; assuming Asia/Kolkata"
        )
        now_local = IST.localize(now_local)

    # Parse and range-check together. Splitting them leaves "25:00" parsing
    # cleanly as two ints and then raising from replace() below, which the
    # caller's broad `except Exception` swallows -- so a config typo silently
    # skips the MIS square-off instead of falling back.
    try:
        expiry_hour, expiry_minute = map(int, session_expiry_str.split(":"))
        boundary_today = now_local.replace(
            hour=expiry_hour, minute=expiry_minute, second=0, microsecond=0
        )
    except ValueError:
        logger.warning(
            f"Invalid SESSION_EXPIRY_TIME format: {session_expiry_str}. "
            "Using default 03:00"
        )
        boundary_today = now_local.replace(hour=3, minute=0, second=0, microsecond=0)
    if now_local >= boundary_today:
        return boundary_today
    return boundary_today - timedelta(days=1)


def last_session_expiry_utc(session_expiry_str, now_local):
    """Resolve the most recent session boundary as a naive UTC datetime.

    For columns actually written by ``func.now()`` (naive UTC / ``CURRENT_TIMESTAMP``),
    e.g. ``sandbox_positions.created_at``/``updated_at``.

    Args:
        session_expiry_str: Wall-clock boundary from config (e.g. '03:00').
        now_local: Timezone-aware current time in the host's timezone.

    Returns:
        Naive UTC datetime of the most recent session expiry.
    """
    return as_db_utc(_last_session_expiry_boundary(session_expiry_str, now_local))


def last_session_expiry_local(session_expiry_str, now_local):
    """Resolve the most recent session boundary as a naive local (IST) datetime.

    For columns that are always explicitly stamped with naive IST wall-clock time at
    insert, never actually left to the ``func.now()`` default: ``SandboxOrders
    .order_timestamp``/``update_timestamp`` and ``SandboxTrades.trade_timestamp``
    (see ``sandbox/order_manager.py`` and ``sandbox/execution_engine.py``, which both
    write ``datetime.now(pytz.timezone("Asia/Kolkata"))``). Comparing those columns
    against a UTC-converted boundary silently skews by IST's +5:30 offset, exactly the
    mistake ``as_db_utc``'s docstring warns about, just in the other direction.

    Args:
        session_expiry_str: Wall-clock boundary from config (e.g. '03:00').
        now_local: Timezone-aware current time in the host's timezone.

    Returns:
        Naive IST datetime of the most recent session expiry.
    """
    return _last_session_expiry_boundary(session_expiry_str, now_local).replace(tzinfo=None)
