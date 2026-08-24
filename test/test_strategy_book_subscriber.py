"""Regression test for the event-bus path that feeds the strategy book.

Every existing portfolio-ledger test calls `record_order_tag`/`apply_fill`
directly with an explicit user_id, bypassing
`subscribers/strategy_book_subscriber.py` entirely. That left a real bug
uncaught: `on_order_placed` resolved user_id from
`event.request_data["user_id"]`, a field no real caller (apikey-authed
TradingView/Amibroker/api/v1/Flow traffic) ever sends, so every
StrategyOrderTag/StrategyClosedTrade row written through the real
order-placement path in production carried user_id="" - only discovered by
placing one real strategy-tagged sandbox order through
`services/place_order_service.place_order_with_auth` end to end (see
.claude/backlog/items/2026-08-22-profit-accumulation-portfolio-ledger.md,
2026-08-24 attempt). This test exercises the subscriber the way the real
event bus does: through `on_order_placed`/`on_order_update` with an event
that only carries `api_key`, the field every real order event actually has.

Run with: uv run pytest test/test_strategy_book_subscriber.py -v
"""

import uuid

import pytest


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    from database.auth_db import init_db as init_auth_db
    from database.strategy_book_db import init_strategy_book_db

    init_auth_db()
    init_strategy_book_db()
    yield


@pytest.fixture
def user_id():
    return f"test-subscriber-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def api_key(user_id):
    from database.auth_db import upsert_api_key

    key = f"test-subscriber-key-{uuid.uuid4().hex}"
    upsert_api_key(user_id, key)
    return key


class _FakeOrderPlacedEvent:
    """Stand-in for events.OrderPlacedEvent carrying only what real
    production traffic actually populates - api_key, not request_data."""

    def __init__(self, *, api_key, strategy, orderid, symbol, exchange, product):
        self.api_key = api_key
        self.strategy = strategy
        self.orderid = orderid
        self.symbol = symbol
        self.exchange = exchange
        self.product = product
        self.request_data = {}


def test_on_order_placed_resolves_user_id_from_api_key(api_key, user_id):
    """A real caller's event never sets request_data["user_id"] - the
    subscriber must resolve it from event.api_key instead."""
    from database.strategy_book_db import get_order_tag
    from subscribers.strategy_book_subscriber import on_order_placed

    orderid = f"SUB-TEST-{uuid.uuid4().hex[:10]}"
    on_order_placed(
        _FakeOrderPlacedEvent(
            api_key=api_key,
            strategy="subscriber_test_strategy",
            orderid=orderid,
            symbol="SYM",
            exchange="NFO",
            product="NRML",
        )
    )

    tag = get_order_tag(orderid)
    assert tag is not None
    assert tag.user_id == user_id
    assert tag.strategy == "subscriber_test_strategy"


def test_on_order_placed_falls_back_to_request_data_user_id_without_api_key():
    """No api_key on the event: keep the old request_data["user_id"] lookup
    as a fallback rather than silently dropping the tag."""
    from database.strategy_book_db import get_order_tag
    from subscribers.strategy_book_subscriber import on_order_placed

    orderid = f"SUB-TEST-FALLBACK-{uuid.uuid4().hex[:10]}"
    event = _FakeOrderPlacedEvent(
        api_key="",
        strategy="subscriber_fallback_strategy",
        orderid=orderid,
        symbol="SYM",
        exchange="NFO",
        product="NRML",
    )
    event.request_data = {"user_id": "explicit-user-id"}
    on_order_placed(event)

    tag = get_order_tag(orderid)
    assert tag is not None
    assert tag.user_id == "explicit-user-id"


def test_on_order_placed_with_invalid_api_key_does_not_crash():
    """An unresolvable api_key must not raise out of the event-bus callback
    (the executor swallows/logs exceptions, but the tag should simply be
    unattributed rather than the whole dispatch blowing up)."""
    from database.strategy_book_db import get_order_tag
    from subscribers.strategy_book_subscriber import on_order_placed

    orderid = f"SUB-TEST-BADKEY-{uuid.uuid4().hex[:10]}"
    on_order_placed(
        _FakeOrderPlacedEvent(
            api_key="not-a-real-api-key",
            strategy="subscriber_badkey_strategy",
            orderid=orderid,
            symbol="SYM",
            exchange="NFO",
            product="NRML",
        )
    )

    tag = get_order_tag(orderid)
    assert tag is not None
    assert tag.user_id == ""
