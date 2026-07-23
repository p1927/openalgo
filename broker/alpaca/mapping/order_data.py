"""OpenAlgo ↔ Alpaca order and position mapping."""

from __future__ import annotations

from utils.logging import get_logger

logger = get_logger(__name__)

_ALPACA_STATUS = {
    "new": "open",
    "accepted": "open",
    "pending_new": "open",
    "partially_filled": "open",
    "filled": "complete",
    "done_for_day": "complete",
    "canceled": "cancelled",
    "expired": "cancelled",
    "replaced": "open",
    "pending_cancel": "open",
    "pending_replace": "open",
    "rejected": "rejected",
}


def normalize_us_symbol(symbol: str) -> str:
    clean = (symbol or "").strip().upper()
    return clean.replace(".", "-") if "." in clean else clean


def transform_openalgo_order(data: dict) -> dict:
    """Map OpenAlgo place-order payload to Alpaca POST /v2/orders body."""
    pricetype = (data.get("pricetype") or "MARKET").upper()
    body: dict = {
        "symbol": normalize_us_symbol(data["symbol"]),
        "qty": str(int(data["quantity"])),
        "side": data["action"].lower(),
        "time_in_force": "day",
    }

    if pricetype == "MARKET":
        body["type"] = "market"
    elif pricetype == "LIMIT":
        body["type"] = "limit"
        body["limit_price"] = str(float(data.get("price") or 0))
    elif pricetype == "SL":
        body["type"] = "stop_limit"
        body["limit_price"] = str(float(data.get("price") or 0))
        body["stop_price"] = str(float(data.get("trigger_price") or 0))
    elif pricetype == "SL-M":
        body["type"] = "stop"
        body["stop_price"] = str(float(data.get("trigger_price") or 0))
    else:
        body["type"] = "market"

    return body


def _map_product(exchange: str) -> str:
    return "CNC"


def map_order_data(order_data):  # noqa: ANN001
    if order_data is None:
        return []
    if isinstance(order_data, dict) and order_data.get("status") == "error":
        return []
    if not isinstance(order_data, list):
        logger.warning("Expected list of Alpaca orders, got %s", type(order_data))
        return []

    mapped = []
    for order in order_data:
        if not isinstance(order, dict):
            continue
        exchange = (order.get("exchange") or "NASDAQ").upper()
        status_raw = (order.get("status") or "").lower()
        mapped.append(
            {
                "orderId": order.get("id") or order.get("orderId"),
                "tradingSymbol": order.get("symbol") or "",
                "exchangeSegment": exchange,
                "productType": _map_product(exchange),
                "transactionType": (order.get("side") or "").upper(),
                "orderType": (order.get("type") or "market").upper(),
                "orderStatus": _ALPACA_STATUS.get(status_raw, status_raw),
                "quantity": int(float(order.get("qty") or 0)),
                "filledQty": int(float(order.get("filled_qty") or 0)),
                "price": float(order.get("limit_price") or order.get("filled_avg_price") or 0),
            }
        )
    return mapped


def map_position_data(position_data):  # noqa: ANN001
    return map_order_data(position_data)


def transform_positions_data(positions_data):  # noqa: ANN001
    if positions_data is None:
        return []
    if not isinstance(positions_data, list):
        return []

    rows = []
    for pos in positions_data:
        if not isinstance(pos, dict):
            continue
        qty_raw = float(pos.get("qty") or 0)
        side = (pos.get("side") or "").lower()
        signed_qty = int(qty_raw) if side != "short" else -int(qty_raw)
        exchange = (pos.get("exchange") or "NASDAQ").upper()
        rows.append(
            {
                "symbol": pos.get("symbol") or pos.get("tradingSymbol") or "",
                "exchange": exchange,
                "product": _map_product(exchange),
                "quantity": signed_qty,
                "average_price": float(pos.get("avg_entry_price") or pos.get("average_price") or 0),
                "ltp": float(pos.get("current_price") or pos.get("ltp") or 0),
                "pnl": float(pos.get("unrealized_pl") or pos.get("pnl") or 0),
            }
        )
    return rows
