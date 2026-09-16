"""OpenAlgo order-book timestamps: write and read sides must agree on one time zone.

``SandboxOrders.order_timestamp`` is stamped explicitly at insert time
(``sandbox/order_manager.py``: ``order_timestamp=datetime.now(pytz.timezone("Asia/Kolkata"))``),
never left to the column's ``func.now()`` default. SQLite drops the tzinfo on write, so the
stored value is naive **IST** wall-clock time.

``get_orderbook()``'s own session-window filter used to convert its IST boundary to naive UTC
before comparing it against that column, on the (wrong) assumption that ``order_timestamp`` was
naive UTC like ``sandbox_positions.updated_at`` really is. That skewed the comparison by IST's
+5:30 offset, so an order placed between the true session start and 5:30 later than it could be
silently excluded from "today's" order book — and, downstream, the same string this function
returns is what ``nautilus_openalgo_bridge.reconcile.sweep_stale_orders`` ages to decide whether
a resting order should be auto-cancelled, so a naive-UTC misread there let a stale order's
apparent age go negative and never cross the cancel threshold
([[2026-09-16-openalgo-order-timestamp-zone-mismatch]]).

This test drives the *real* write path (an actual ``SandboxOrders`` row stamped exactly the way
``order_manager.py`` stamps one) through the *real* read path (``OrderManager.get_orderbook()``),
rather than asserting against a hand-built naive datetime that could dodge the bug by picking the
"right" zone by accident.
"""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sandbox.session_boundary import IST, last_session_expiry_local, last_session_expiry_utc  # noqa: E402

TEST_USER = "TEST_ORDER_TS_BOUNDARY_1"

# `pytz.timezone("Asia/Kolkata")` (session_boundary.IST) must never be attached via the
# `tzinfo=` constructor kwarg - pytz's classic pitfall gives an LMT+5:53 offset that way, not
# +5:30. Test-side datetimes here use `zoneinfo`, exactly like test_position_session_boundary.py.
_IST_ZONE = ZoneInfo("Asia/Kolkata")


class TestLastSessionExpiryLocal:
    """Mirrors TestBoundaryConversion in test_position_session_boundary.py, for the naive-IST
    (not naive-UTC) convention that SandboxOrders.order_timestamp / SandboxTrades.trade_timestamp
    actually use."""

    def test_returns_naive_ist_not_utc(self):
        now = datetime(2026, 8, 12, 14, 7, tzinfo=_IST_ZONE)
        # The most recent 03:00 IST boundary, still expressed in IST (naive) -
        # not converted to 21:30 UTC the day before, which last_session_expiry_utc gives.
        assert last_session_expiry_local("03:00", now) == datetime(2026, 8, 12, 3, 0)
        assert last_session_expiry_utc("03:00", now) == datetime(2026, 8, 11, 21, 30)

    def test_before_expiry_rolls_back_a_day(self):
        now = datetime(2026, 8, 12, 1, 0, tzinfo=_IST_ZONE)
        assert last_session_expiry_local("03:00", now) == datetime(2026, 8, 11, 3, 0)

    def test_naive_input_is_treated_as_ist(self):
        now = datetime(2026, 8, 12, 14, 7)  # naive
        assert last_session_expiry_local("03:00", now) == datetime(2026, 8, 12, 3, 0)


@pytest.fixture()
def orders_around_boundary():
    """Insert SandboxOrders rows through the exact same write path order_manager.py uses,
    aged to straddle the real current session boundary, whatever wall clock the suite runs
    under."""
    from database.sandbox_db import SandboxOrders, db_session

    SandboxOrders.query.filter_by(user_id=TEST_USER).delete()
    db_session.commit()

    session_expiry_str = os.getenv("SESSION_EXPIRY_TIME", "03:00")
    boundary_ist = last_session_expiry_local(session_expiry_str, datetime.now(IST))
    # boundary_ist is naive; stamp rows the same way order_manager.py does — an
    # explicit pytz-aware IST datetime, which SQLite will store with the tzinfo
    # dropped (a naive value equal to boundary_ist's wall-clock reading).
    ist = pytz.timezone("Asia/Kolkata")

    def _stamped_row(orderid: str, offset: timedelta):
        naive_ist_value = boundary_ist + offset
        return SandboxOrders(
            orderid=orderid,
            user_id=TEST_USER,
            symbol="SBTEST_ORD",
            exchange="NSE",
            action="BUY",
            quantity=1,
            price=None,
            price_type="MARKET",
            product="MIS",
            order_status="open",
            filled_quantity=0,
            pending_quantity=1,
            margin_blocked=0,
            order_timestamp=ist.localize(naive_ist_value),
        )

    rows = [
        # Placed just after this session's boundary -> current session, must be shown.
        _stamped_row("SBORD_IN_SESSION", timedelta(minutes=30)),
        # Placed just before the boundary -> previous session, must be hidden.
        _stamped_row("SBORD_PREV_SESSION", timedelta(minutes=-30)),
    ]
    for row in rows:
        db_session.add(row)
    db_session.commit()

    yield

    SandboxOrders.query.filter_by(user_id=TEST_USER).delete()
    db_session.commit()


def test_get_orderbook_session_filter_matches_the_real_write_path(orders_around_boundary):
    from sandbox.order_manager import OrderManager

    ok, response, status = OrderManager(TEST_USER).get_orderbook()
    assert ok, response
    assert status == 200
    order_ids = {o["orderid"] for o in response["data"]["orders"]}
    assert "SBORD_IN_SESSION" in order_ids
    assert "SBORD_PREV_SESSION" not in order_ids


def test_orderbook_timestamp_field_is_naive_ist_wall_clock(orders_around_boundary):
    """The "timestamp" string get_orderbook() hands external readers (including the Trade
    nautilus_openalgo_bridge) must be the naive IST wall-clock value it was stamped with -
    this is the contract the bridge's own _parse_order_timestamp relies on
    (see tests/test_nautilus_reconcile_stale_order_timezone.py in the Trade repo)."""
    from sandbox.order_manager import OrderManager

    ok, response, status = OrderManager(TEST_USER).get_orderbook()
    assert ok, response
    row = next(o for o in response["data"]["orders"] if o["orderid"] == "SBORD_IN_SESSION")
    timestamp_str = row["timestamp"]  # e.g. "2026-09-16 03:30:00"

    session_expiry_str = os.getenv("SESSION_EXPIRY_TIME", "03:00")
    boundary_ist = last_session_expiry_local(session_expiry_str, datetime.now(IST))
    expected = (boundary_ist + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    assert timestamp_str == expected
