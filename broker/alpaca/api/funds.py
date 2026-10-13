"""Alpaca account funds — /v2/account mapped to OpenAlgo margin shape."""

from __future__ import annotations

from broker.alpaca.api._http import alpaca_request
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

    cash = float(payload.get("cash") or 0)
    buying_power = float(payload.get("buying_power") or 0)
    equity = float(payload.get("equity") or 0)
    utilised = max(0.0, equity - cash)

    total_unrealized = 0.0
    positions = get_positions(auth_token)
    if not isinstance(positions, list):
        raise RuntimeError(f"Alpaca positions fetch failed: {positions}")
    for pos in positions:
        try:
            total_unrealized += float(pos.get("unrealized_pl") or 0)
        except (TypeError, ValueError):
            continue

    return {
        "availablecash": f"{buying_power:.2f}",
        "collateral": f"{cash:.2f}",
        "m2munrealized": f"{total_unrealized:.2f}",
        "m2mrealized": "0.00",
        "utiliseddebits": f"{utilised:.2f}",
    }
