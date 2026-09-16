"""Regression test for the sandbox session-expiry timezone mismatch.

``get_open_positions`` (``sandbox/position_manager.py``) computes a "last session
expiry" boundary and compares it against ``SandboxPositions.updated_at``, which
*is* stamped via SQLAlchemy's ``func.now()`` -- naive **UTC**
(``CURRENT_TIMESTAMP``) on SQLite.

``get_tradebook`` / ``get_orderbook`` compare against ``SandboxTrades
.trade_timestamp`` / ``SandboxOrders.order_timestamp`` instead. Those two columns
are never actually left to their ``func.now()`` default: every insert
(``sandbox/execution_engine.py``, ``sandbox/order_manager.py``) stamps them
explicitly with ``datetime.now(pytz.timezone("Asia/Kolkata"))``, so the value
SQLite stores (tzinfo dropped on write) is naive **IST** wall-clock time, not
naive UTC -- see [[2026-09-16-openalgo-order-timestamp-zone-mismatch]]. The
boundary for those two reads must therefore stay in IST
(``last_session_expiry_local``), not be converted to UTC like the positions
boundary is (``last_session_expiry_utc``).

The boundary used to be computed from naive ``datetime.now()`` (server-local
time), with no conversion at all. On a server whose OS timezone is IST -- the
environment this bug was actually observed in -- ``datetime.now()`` returns IST
wall-clock numbers, so the boundary for the *positions* comparison ended up
~5.5 hours ahead of the UTC ``updated_at`` value it is compared against. During
the IST 00:00-05:29 window this pushed the boundary's calendar date a full day
ahead, so a position from the current session looked like it predated the last
session expiry and was silently dropped.

This test freezes "now" -- both the bare ``datetime.now()`` OpenAlgo's own
session-boundary code used to call and the timezone-aware ``datetime.now(ist)``
the fix now calls -- to simulate a server with IST as its OS-local timezone, at
03:40 IST (inside the bug window, just after the 03:00 default session-expiry
cutoff). It plants a position/order/trade stamped a few minutes "in the past"
relative to that instant (i.e. genuinely part of the current session), in
each row's own real write convention (UTC for positions, IST for
orders/trades), and confirms each of the three read paths still surfaces it,
rather than dropping it as stale.
"""

from datetime import datetime as real_datetime
from datetime import timedelta
from decimal import Decimal

import pytest
import pytz

from database.sandbox_db import (
    SandboxOrders,
    SandboxPositions,
    SandboxTrades,
    db_session,
)
from sandbox.order_manager import OrderManager
from sandbox.position_manager import PositionManager

IST = pytz.timezone("Asia/Kolkata")

USER_ID = "session-expiry-tz-test-user"
SYMBOL = "RELIANCE"
EXCHANGE = "NSE"

# The instant the test freezes "now" to: 2026-09-03 03:40 IST. Inside the
# IST 00:00-05:29 bug window and after the default 03:00 SESSION_EXPIRY_TIME,
# so the "last session expiry" boundary is today (IST) at 03:00.
FROZEN_UTC_INSTANT = IST.localize(real_datetime(2026, 9, 3, 3, 40, 0)).astimezone(pytz.utc)

# The position was stamped 5 minutes before the frozen instant, in real UTC
# (as func.now() would have stored it) -- squarely inside the current session,
# well after the correct (UTC) session boundary.
RECENT_UTC_TIMESTAMP = (FROZEN_UTC_INSTANT - timedelta(minutes=5)).replace(tzinfo=None)

# The order/trade was stamped 5 minutes before the frozen instant too, but in
# naive IST wall-clock time -- the real convention order_manager.py /
# execution_engine.py write (see module docstring above), not naive UTC.
RECENT_IST_TIMESTAMP = (
    (FROZEN_UTC_INSTANT - timedelta(minutes=5)).astimezone(IST).replace(tzinfo=None)
)


class _FrozenDatetime(real_datetime):
    """A ``datetime.datetime`` stand-in frozen at ``FROZEN_UTC_INSTANT``.

    ``now(tz=None)`` simulates an OS whose local timezone is IST (the
    environment the bug was observed in), returning naive IST wall-clock
    numbers. ``now(tz=...)`` returns the real instant converted to that
    timezone, matching real ``datetime.now(tz)`` semantics.
    """

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FROZEN_UTC_INSTANT.astimezone(IST).replace(tzinfo=None)
        return FROZEN_UTC_INSTANT.astimezone(tz)


def _freeze_now(monkeypatch):
    """Freeze both naive-local and timezone-aware ``datetime.now()``.

    Applied only around the call under test (never around row setup/teardown):
    SQLAlchemy's SQLite datetime bind processor does an ``isinstance`` check
    against ``datetime.datetime`` at insert time, and swapping that class out
    from under it corrupts unrelated writes (silently truncating the stored
    timestamp to midnight) -- confirmed directly while writing this test.
    """
    import datetime as datetime_module

    monkeypatch.setattr(datetime_module, "datetime", _FrozenDatetime)


@pytest.fixture
def cleanup_rows():
    yield
    SandboxPositions.query.filter_by(user_id=USER_ID).delete()
    SandboxOrders.query.filter_by(user_id=USER_ID).delete()
    SandboxTrades.query.filter_by(user_id=USER_ID).delete()
    db_session.commit()


def test_get_open_positions_keeps_position_from_current_ist_session(
    monkeypatch, cleanup_rows
):
    """A position updated moments ago (real UTC) must not look stale at 03:40 IST."""
    SandboxPositions.query.filter_by(user_id=USER_ID).delete()
    db_session.commit()

    db_session.add(
        SandboxPositions(
            user_id=USER_ID,
            symbol=SYMBOL,
            exchange=EXCHANGE,
            product="CNC",
            quantity=3,
            average_price=Decimal("1000.00"),
            ltp=Decimal("1000.00"),
            pnl=Decimal("0.00"),
            today_realized_pnl=Decimal("0.00"),
            margin_blocked=Decimal("0.00"),
            created_at=RECENT_UTC_TIMESTAMP,
            updated_at=RECENT_UTC_TIMESTAMP,
        )
    )
    db_session.commit()

    _freeze_now(monkeypatch)
    success, response, code = PositionManager(USER_ID).get_open_positions(update_mtm=False)
    monkeypatch.undo()

    assert success is True
    assert code == 200
    symbols = [p["symbol"] for p in response["data"]]
    assert SYMBOL in symbols, (
        "position opened moments ago (real UTC) was dropped as belonging to a "
        "prior session -- the session-expiry boundary is not timezone-consistent"
    )


def test_get_tradebook_keeps_trade_from_current_ist_session(monkeypatch, cleanup_rows):
    SandboxTrades.query.filter_by(user_id=USER_ID).delete()
    db_session.commit()

    db_session.add(
        SandboxTrades(
            tradeid="tz-test-trade-1",
            orderid="tz-test-order-1",
            user_id=USER_ID,
            symbol=SYMBOL,
            exchange=EXCHANGE,
            action="BUY",
            quantity=3,
            price=Decimal("1000.00"),
            product="CNC",
            trade_timestamp=RECENT_IST_TIMESTAMP,
        )
    )
    db_session.commit()

    _freeze_now(monkeypatch)
    success, response, code = PositionManager(USER_ID).get_tradebook()
    monkeypatch.undo()

    assert success is True
    assert code == 200
    tradeids = [t["tradeid"] for t in response["data"]]
    assert "tz-test-trade-1" in tradeids, (
        "trade executed moments ago (real IST wall-clock time) was excluded from the current "
        "session's tradebook -- the session-start boundary is not "
        "timezone-consistent"
    )


def test_get_orderbook_keeps_order_from_current_ist_session(monkeypatch, cleanup_rows):
    SandboxOrders.query.filter_by(user_id=USER_ID).delete()
    db_session.commit()

    db_session.add(
        SandboxOrders(
            orderid="tz-test-order-1",
            user_id=USER_ID,
            symbol=SYMBOL,
            exchange=EXCHANGE,
            action="BUY",
            quantity=3,
            price=Decimal("1000.00"),
            trigger_price=Decimal("0.00"),
            price_type="MARKET",
            product="CNC",
            order_status="complete",
            average_price=Decimal("1000.00"),
            filled_quantity=3,
            pending_quantity=0,
            order_timestamp=RECENT_IST_TIMESTAMP,
            update_timestamp=RECENT_IST_TIMESTAMP,
        )
    )
    db_session.commit()

    _freeze_now(monkeypatch)
    success, response, code = OrderManager(USER_ID).get_orderbook()
    monkeypatch.undo()

    assert success is True
    assert code == 200
    orderids = [o["orderid"] for o in response["data"]["orders"]]
    assert "tz-test-order-1" in orderids, (
        "order placed moments ago (real IST wall-clock time) was excluded from the current "
        "session's orderbook -- the session-start boundary is not "
        "timezone-consistent"
    )
