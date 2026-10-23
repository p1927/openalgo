"""Alpaca account funds — /v2/account mapped to OpenAlgo margin shape."""

from __future__ import annotations

from decimal import Decimal

from broker.alpaca.api._http import alpaca_request, required_decimal
from broker.alpaca.api.baseurl import trade_base
from broker.alpaca.api.order_api import get_positions
from utils.logging import get_logger

logger = get_logger(__name__)

def test_auth_token(auth_token: str) -> tuple[bool, str | None]:
    from broker.alpaca.api.auth_api import test_auth_token as _test

    return _test(auth_token)


def get_margin_data(auth_token: str) -> dict[str, str]:
    url = f"{trade_base().rstrip('/')}/v2/account"
    status, payload = alpaca_request("GET", url, auth_token)
    if status >= 400 or not isinstance(payload, dict):
        # Raise, don't report zero funds: funds_service turns this into an error response, whereas
        # a "0.00" balance reads as a real empty account.
        raise RuntimeError(f"Alpaca account fetch failed ({status}): {payload}")

    cash = required_decimal(payload, "cash")
    buying_power = required_decimal(payload, "buying_power")
    # OpenAlgo's utiliseddebits is margin blocked for positions. Alpaca reports
    # this directly as Reg T initial margin; equity-cash fails for short books.
    # https://alpaca.markets/sdks/python/api_reference/trading/models.html#alpaca.trading.models.TradeAccount.initial_margin
    utilised = required_decimal(payload, "initial_margin")
    if utilised < 0:
        raise RuntimeError("Alpaca initial_margin is negative")

    total_unrealized = Decimal("0")
    positions = get_positions(auth_token)
    if not isinstance(positions, list):
        raise RuntimeError(f"Alpaca positions fetch failed: {positions}")
    for pos in positions:
        if not isinstance(pos, dict):
            raise RuntimeError("Alpaca position is invalid")
        total_unrealized += required_decimal(pos, "unrealized_pl")

    return {
        # availablecash is real cash; buying_power is a margin multiple (up to 4x) and is reported separately.
        # m2mrealized is omitted, not faked as 0.00: /v2/account carries no booked day P&L (consumers use .get).
        "availablecash": f"{cash:.2f}",
        "buyingpower": f"{buying_power:.2f}",
        "collateral": f"{cash:.2f}",
        "m2munrealized": f"{total_unrealized:.2f}",
        "utiliseddebits": f"{utilised:.2f}",
    }
