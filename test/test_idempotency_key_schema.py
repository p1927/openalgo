"""OrderSchema/BasketOrderSchema accept an optional idempotency_key and drop it."""

import pytest
from marshmallow import ValidationError

from restx_api.schemas import BasketOrderSchema, OrderSchema

ORDER = {"apikey": "k", "strategy": "s", "exchange": "NSE", "symbol": "INFY",
         "action": "BUY", "quantity": 1}


def test_order_key_optional_and_dropped():
    assert "idempotency_key" not in OrderSchema().load(ORDER)
    assert "idempotency_key" not in OrderSchema().load({**ORDER, "idempotency_key": "abc"})


def test_basket_key_optional_and_dropped():
    base = {"apikey": "k", "strategy": "s", "orders": []}
    assert "idempotency_key" not in BasketOrderSchema().load(base)
    assert "idempotency_key" not in BasketOrderSchema().load({**base, "idempotency_key": "abc"})


def test_empty_key_rejected():
    with pytest.raises(ValidationError):
        OrderSchema().load({**ORDER, "idempotency_key": ""})
