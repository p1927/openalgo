"""Alpaca order execution — place, cancel, books, positions."""

from __future__ import annotations

import json
from typing import Any

from broker.alpaca.api._http import alpaca_request
from broker.alpaca.api.baseurl import trade_base
from broker.alpaca.mapping.order_data import normalize_us_symbol, transform_openalgo_order
from utils.logging import get_logger

logger = get_logger(__name__)


class _HttpResponse:
    def __init__(self, status_code: int, text: str):
        self.status = status_code
        self.status_code = status_code
        self.text = text


def _trade_url(path: str) -> str:
    base = trade_base().rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def get_order_book(auth):  # noqa: ANN001
    status, payload = alpaca_request("GET", _trade_url("/v2/orders"), auth, params={"status": "all"})
    if status >= 400:
        logger.error("Alpaca order book error: %s", payload)
        return []
    return payload if isinstance(payload, list) else []


def get_trade_book(auth):  # noqa: ANN001
    status, payload = alpaca_request("GET", _trade_url("/v2/account/activities"), auth)
    if status >= 400:
        return []
    if isinstance(payload, list):
        return [row for row in payload if row.get("activity_type") == "FILL"]
    return []


def get_positions(auth):  # noqa: ANN001
    status, payload = alpaca_request("GET", _trade_url("/v2/positions"), auth)
    if status >= 400:
        logger.error("Alpaca positions error: %s", payload)
        return {"status": "error", "message": str(payload)}
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict) and "exchange" not in row:
                row["exchange"] = "NASDAQ"
        return payload
    return []


def get_holdings(auth):  # noqa: ANN001
    return get_positions(auth)


def place_order_api(data, auth):  # noqa: ANN001
    body = transform_openalgo_order(data)
    url = _trade_url("/v2/orders")
    status, payload = alpaca_request("POST", url, auth, json_body=body)
    res = _HttpResponse(status, json.dumps(payload))
    orderid = payload.get("id") if isinstance(payload, dict) else None
    if status >= 400:
        logger.error("Alpaca place order failed: %s", payload)
    return res, payload, orderid


def cancel_order(orderid, auth):  # noqa: ANN001
    url = _trade_url(f"/v2/orders/{orderid}")
    status, payload = alpaca_request("DELETE", url, auth)
    if status in (200, 204):
        return {"status": "success", "orderid": orderid}, 200
    message = payload.get("message", "Failed to cancel order") if isinstance(payload, dict) else "Failed"
    return {"status": "error", "message": message}, status or 500


def close_position(symbol, auth):  # noqa: ANN001
    clean = normalize_us_symbol(symbol)
    url = _trade_url(f"/v2/positions/{clean}")
    status, payload = alpaca_request("DELETE", url, auth)
    return status, payload
