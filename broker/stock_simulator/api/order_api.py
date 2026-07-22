"""Order API stub — analyzer mode routes orders to OpenAlgo sandbox."""

from __future__ import annotations

from utils.logging import get_logger

logger = get_logger(__name__)


class _StubResponse:
    status = 501

    def __init__(self, message: str):
        self.text = message


def place_order_api(order_data, auth_token):  # noqa: ANN001
    logger.warning("stock_simulator live order path invoked — enable Analyzer mode")
    return _StubResponse("enable analyzer"), {"status": "error", "message": "Use Analyzer mode"}, None


def get_order_book(auth_token):  # noqa: ANN001
    return []

def get_trade_book(auth_token):  # noqa: ANN001
    return []

def get_positions(auth_token):  # noqa: ANN001
    return []

def get_holdings(auth_token):  # noqa: ANN001
    return []
