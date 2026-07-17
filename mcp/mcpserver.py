import json
import os
import sys
from datetime import date, timedelta
from typing import Any

import httpx
import pandas as pd
from mcp.server.fastmcp import FastMCP
from openalgo import api, ta

# Two boot paths share this module:
#
# 1. Stdio (legacy / local) — Claude Desktop, Cursor, Windsurf spawn this
#    file as `python -m mcp.mcpserver <api_key> <host>`. argv[1] and argv[2]
#    must be present. The original MCP integration lives here unchanged.
#
# 2. HTTP / SSE — blueprints/mcp_http.py imports this module to access the
#    `mcp` FastMCP instance (and every @mcp.tool decorated function), then
#    calls init_for_http(api_key, host) once per process to wire the SDK
#    client. The Flask app sets OPENALGO_MCP_HTTP_BOOT=1 *before* importing
#    so the argv check is bypassed.
#
# The branching is at module scope rather than inside a function so the
# FastMCP `mcp = FastMCP(...)` instance and every `@mcp.tool` decorator
# remain top-level (FastMCP relies on import-time registration).

if os.environ.get("OPENALGO_MCP_HTTP_BOOT") == "1":
    # HTTP transport — Flask sets the env var before import. The SDK
    # client is wired by init_for_http() right after import. Stdio
    # users never hit this branch.
    api_key: str | None = None
    host: str | None = None
    client = None
else:
    # === Original stdio behavior — preserved verbatim ===
    # Existing Claude Desktop / Cursor / Windsurf integrations launch
    # this file as `python -m mcp.mcpserver <api_key> <host>` and rely
    # on this exact error path on misconfiguration. Do NOT change the
    # check, the order, or the error message here.
    if len(sys.argv) < 3:
        raise ValueError("API key and host must be provided as command line arguments")

    api_key = sys.argv[1]
    host = sys.argv[2]

    # Initialize OpenAlgo pip SDK client — execution tools only (orders, margin,
    # positionbook, funds). India market-data MCP tools route through
    # trade_integrations.hub_capture.channel instead of this client.
    client = api(api_key=api_key, host=host)


def init_for_http(api_key_value: str, host_value: str) -> None:
    """Wire the SDK client when running under the HTTP transport.

    Called once from blueprints/mcp_http.py after the Flask app has
    determined the admin's API key and the local OpenAlgo loopback URL.
    Idempotent — safe to call repeatedly with the same values; later
    calls overwrite the global so a restarted broker session can rotate
    the underlying SDK client without restarting Gunicorn.
    """
    global api_key, host, client
    api_key = api_key_value
    host = host_value
    client = api(api_key=api_key_value, host=host_value)

# Default strategy name for all order-related calls originating from the MCP server.
# Surfaced in OpenAlgo logs and analyzer views so MCP-driven trades are identifiable.
MCP_STRATEGY = "python mcp"

# OpenAlgo standardized index symbols (NSE_INDEX / BSE_INDEX) — rolled out across all brokers.
# Source: https://docs.openalgo.in/symbol-format
NSE_INDEX_SYMBOLS = [
    "NIFTY", "NIFTYNXT50", "FINNIFTY", "BANKNIFTY", "MIDCPNIFTY", "INDIAVIX",
    "HANGSENGBEESNAV",
    "NIFTY100", "NIFTY200", "NIFTY500",
    "NIFTYALPHA50", "NIFTYAUTO", "NIFTYCOMMODITIES", "NIFTYCONSUMPTION",
    "NIFTYCPSE", "NIFTYDIVOPPS50", "NIFTYENERGY", "NIFTYFMCG",
    "NIFTYGROWSECT15",
    "NIFTYGS10YR", "NIFTYGS10YRCLN", "NIFTYGS1115YR", "NIFTYGS15YRPLUS",
    "NIFTYGS48YR", "NIFTYGS813YR", "NIFTYGSCOMPSITE",
    "NIFTYINFRA", "NIFTYIT", "NIFTYMEDIA", "NIFTYMETAL",
    "NIFTYMIDLIQ15", "NIFTYMIDCAP100", "NIFTYMIDCAP150", "NIFTYMIDCAP50",
    "NIFTYMIDSML400", "NIFTYMNC", "NIFTYPHARMA", "NIFTYPSE", "NIFTYPSUBANK",
    "NIFTYPVTBANK", "NIFTYREALTY", "NIFTYSERVSECTOR",
    "NIFTYSMLCAP100", "NIFTYSMLCAP250", "NIFTYSMLCAP50",
    "NIFTY100EQLWGT", "NIFTY100LIQ15", "NIFTY100LOWVOL30",
    "NIFTY100QUALTY30", "NIFTY200QUALTY30",
    "NIFTY50DIVPOINT", "NIFTY50EQLWGT",
    "NIFTY50PR1XINV", "NIFTY50PR2XLEV", "NIFTY50TR1XINV", "NIFTY50TR2XLEV",
    "NIFTY50VALUE20",
]
BSE_INDEX_SYMBOLS = [
    "SENSEX", "BANKEX", "SENSEX50",
    "BSE100", "BSE150MIDCAPINDEX", "BSE200", "BSE250LARGEMIDCAPINDEX",
    "BSE400MIDSMALLCAPINDEX", "BSE500",
    "BSEAUTO", "BSECAPITALGOODS", "BSECARBONEX", "BSECONSUMERDURABLES",
    "BSECPSE", "BSEDOLLEX100", "BSEDOLLEX200", "BSEDOLLEX30",
    "BSEENERGY", "BSEFASTMOVINGCONSUMERGOODS", "BSEFINANCIALSERVICES",
    "BSEGREENEX", "BSEHEALTHCARE", "BSEINDIAINFRASTRUCTUREINDEX",
    "BSEINDUSTRIALS", "BSEINFORMATIONTECHNOLOGY", "BSEIPO",
    "BSELARGECAP", "BSEMETAL", "BSEMIDCAP", "BSEMIDCAPSELECTINDEX",
    "BSEOIL&GAS", "BSEPOWER", "BSEPSU", "BSEREALTY", "BSESENSEXNEXT50",
    "BSESMALLCAP", "BSESMALLCAPSELECTINDEX", "BSESMEIPO",
    "BSETECK", "BSETELECOM",
]

# Create MCP server
mcp = FastMCP("openalgo")


def _to_json(payload: Any) -> str:
    """Serialize any SDK response (dict, list, or pandas DataFrame) to a JSON string."""
    if hasattr(payload, "to_dict") and hasattr(payload, "reset_index"):
        df = payload.reset_index()
        return json.dumps(
            {"count": len(df), "data": df.to_dict(orient="records")},
            indent=2,
            default=str,
        )
    return json.dumps(payload, indent=2, default=str)

# ORDER MANAGEMENT TOOLS — pip SDK `client` (execution authority)


@mcp.tool()
def place_order(
    symbol: str,
    quantity: int,
    action: str,
    exchange: str = "NSE",
    price_type: str = "MARKET",
    product: str = "MIS",
    strategy: str = MCP_STRATEGY,
    price: float | None = None,
    trigger_price: float | None = None,
    disclosed_quantity: int | None = None,
) -> str:
    """
    Place a new order (market or limit).

    Args:
        symbol: Stock symbol (e.g., 'RELIANCE')
        quantity: Number of shares
        action: 'BUY' or 'SELL'
        exchange: 'NSE', 'NFO', 'CDS', 'BSE', 'BFO', 'BCD', 'MCX', 'NCDEX'
        price_type: 'MARKET', 'LIMIT', 'SL', 'SL-M'
        product: 'CNC', 'NRML', 'MIS'
        strategy: Strategy name (defaults to 'python mcp')
        price: Limit price (required for LIMIT orders)
        trigger_price: Trigger price (required for SL and SL-M orders)
        disclosed_quantity: Disclosed quantity
    """
    try:
        params = {
            "strategy": strategy,
            "symbol": symbol.upper(),
            "action": action.upper(),
            "exchange": exchange.upper(),
            "price_type": price_type.upper(),
            "product": product.upper(),
            "quantity": quantity,
        }

        if price is not None:
            params["price"] = price
        if trigger_price is not None:
            params["trigger_price"] = trigger_price
        if disclosed_quantity is not None:
            params["disclosed_quantity"] = disclosed_quantity

        response = client.placeorder(**params)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error placing order: {str(e)}"


@mcp.tool()
def place_smart_order(
    symbol: str,
    quantity: int,
    action: str,
    position_size: int,
    exchange: str = "NSE",
    price_type: str = "MARKET",
    product: str = "MIS",
    strategy: str = MCP_STRATEGY,
    price: float | None = None,
    trigger_price: float | None = None,
    disclosed_quantity: int | None = None,
) -> str:
    """
    Place a smart order that considers the current position size (auto-calculates delta
    between requested and current size before sending to the broker).

    Args:
        symbol: Stock symbol
        quantity: Target quantity
        action: 'BUY' or 'SELL'
        position_size: Current position size
        exchange: Exchange name
        price_type: 'MARKET', 'LIMIT', 'SL', 'SL-M'
        product: 'CNC', 'NRML', 'MIS'
        strategy: Strategy name (defaults to 'python mcp')
        price: Limit price (required for LIMIT orders)
        trigger_price: Trigger price (required for SL / SL-M orders)
        disclosed_quantity: Disclosed quantity
    """
    try:
        params = {
            "strategy": strategy,
            "symbol": symbol.upper(),
            "action": action.upper(),
            "exchange": exchange.upper(),
            "price_type": price_type.upper(),
            "product": product.upper(),
            "quantity": quantity,
            "position_size": position_size,
        }

        if price is not None:
            params["price"] = price
        if trigger_price is not None:
            params["trigger_price"] = trigger_price
        if disclosed_quantity is not None:
            params["disclosed_quantity"] = disclosed_quantity

        response = client.placesmartorder(**params)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error placing smart order: {str(e)}"


@mcp.tool()
def place_basket_order(orders: list[dict[str, Any]], strategy: str = MCP_STRATEGY) -> str:
    """
    Place multiple orders in a basket.

    Args:
        orders: List of order dictionaries. Each order should contain:
            - symbol (str): Trading symbol. Required.
            - exchange (str): Exchange code. Required.
            - action (str): BUY or SELL. Required.
            - quantity (int/str): Quantity to trade. Required.
            - pricetype (str): MARKET, LIMIT, SL, SL-M. Optional, defaults to MARKET.
            - product (str): MIS, CNC, NRML. Optional, defaults to MIS.
            - price (str): Required for LIMIT orders.
            - trigger_price (str): Required for SL orders.
        strategy: Strategy name (default: Python)

        Example: [
            {"symbol": "BHEL", "exchange": "NSE", "action": "BUY", "quantity": 1, "pricetype": "MARKET", "product": "MIS"},
            {"symbol": "ZOMATO", "exchange": "NSE", "action": "SELL", "quantity": 1, "pricetype": "MARKET", "product": "MIS"}
        ]

    Returns:
        JSON with results for each order including orderid, status, and symbol
    """
    try:
        response = client.basketorder(strategy=strategy, orders=orders)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error placing basket order: {str(e)}"


@mcp.tool()
def place_split_order(
    symbol: str,
    quantity: int,
    split_size: int,
    action: str,
    exchange: str = "NSE",
    price_type: str = "MARKET",
    product: str = "MIS",
    strategy: str = MCP_STRATEGY,
    price: float | None = None,
    trigger_price: float | None = None,
    disclosed_quantity: int | None = None,
) -> str:
    """
    Place a large order split into smaller chunks.

    Args:
        symbol: Stock symbol (e.g., 'YESBANK')
        quantity: Total quantity to trade
        split_size: Size of each split order
        action: 'BUY' or 'SELL'
        exchange: Exchange name (default: NSE)
        price_type: 'MARKET', 'LIMIT', 'SL', 'SL-M' (default: MARKET)
        product: 'MIS', 'CNC', 'NRML' (default: MIS)
        strategy: Strategy name (default: Python)
        price: Limit price (required for LIMIT orders)
        trigger_price: Trigger price (required for SL orders)
        disclosed_quantity: Disclosed quantity (optional)

    Returns:
        JSON with results array containing each split order's orderid, quantity, and status

    Example:
        # Split 105 shares into orders of 20 each (5 orders of 20 + 1 order of 5)
        place_split_order("YESBANK", 105, 20, "SELL", "NSE")
    """
    try:
        params = {
            "strategy": strategy,
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
            "action": action.upper(),
            "quantity": quantity,
            "splitsize": split_size,
            "price_type": price_type.upper(),
            "product": product.upper(),
        }

        if price is not None:
            params["price"] = price
        if trigger_price is not None:
            params["trigger_price"] = trigger_price
        if disclosed_quantity is not None:
            params["disclosed_quantity"] = disclosed_quantity

        response = client.splitorder(**params)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error placing split order: {str(e)}"


@mcp.tool()
def place_options_order(
    underlying: str,
    exchange: str,
    offset: str,
    option_type: str,
    action: str,
    quantity: int,
    expiry_date: str | None = None,
    strategy: str = MCP_STRATEGY,
    price_type: str = "MARKET",
    product: str = "MIS",
    price: float | None = None,
    trigger_price: float | None = None,
    disclosed_quantity: int | None = None,
) -> str:
    """
    Place an options order with ATM/ITM/OTM offset.

    Args:
        underlying: Underlying symbol (e.g., 'NIFTY', 'BANKNIFTY', 'NIFTY28OCT25FUT')
        exchange: Exchange for underlying ('NSE_INDEX', 'BSE_INDEX', 'NFO')
        offset: Strike offset - 'ATM', 'ITM1'-'ITM50', 'OTM1'-'OTM50'
        option_type: 'CE' for Call or 'PE' for Put
        action: 'BUY' or 'SELL'
        quantity: Absolute quantity — must be a multiple of the contract lot size.
                  Do NOT hardcode lot size — call get_option_symbol() or get_option_chain()
                  first to read the current 'lotsize' from the broker master contract,
                  then pass quantity = lots * lotsize.
        expiry_date: Expiry date in format 'DDMMMYY' (e.g., '28OCT25'). Optional if underlying includes expiry.
        strategy: Strategy name (default: Python)
        price_type: 'MARKET', 'LIMIT', 'SL', 'SL-M' (default: MARKET)
        product: 'MIS', 'NRML' (default: MIS). Note: CNC not supported for options.
        price: Limit price (required for LIMIT orders)
        trigger_price: Trigger price (required for SL and SL-M orders)
        disclosed_quantity: Disclosed quantity (optional)

    Returns:
        JSON with orderid, symbol, underlying_ltp, offset, option_type, mode

    Example:
        # Basic ATM call order
        place_options_order("NIFTY", "NSE_INDEX", "ATM", "CE", "BUY", 75, "28NOV25")

        # Using future as underlying (expiry auto-detected)
        place_options_order("NIFTY28OCT25FUT", "NFO", "ITM2", "CE", "BUY", 75)
    """
    try:
        params = {
            "strategy": strategy,
            "underlying": underlying.upper(),
            "exchange": exchange.upper(),
            "offset": offset.upper(),
            "option_type": option_type.upper(),
            "action": action.upper(),
            "quantity": quantity,
            "price_type": price_type.upper(),
            "product": product.upper(),
        }

        if expiry_date is not None:
            params["expiry_date"] = expiry_date
        if price is not None:
            params["price"] = price
        if trigger_price is not None:
            params["trigger_price"] = trigger_price
        if disclosed_quantity is not None:
            params["disclosed_quantity"] = disclosed_quantity

        response = client.optionsorder(**params)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error placing options order: {str(e)}"


@mcp.tool()
def place_options_multi_order(
    underlying: str,
    exchange: str,
    legs: list[dict[str, Any]],
    expiry_date: str | None = None,
    strategy: str = MCP_STRATEGY,
) -> str:
    """
    Place a multi-leg options order (spreads, iron condor, straddles, etc.).
    BUY legs are executed first for margin efficiency, then SELL legs.

    Args:
        strategy: Strategy name (defaults to 'python mcp'). Give each multi-leg trade
                  a meaningful name (e.g., 'nifty iron condor') to make tracking easier.
        underlying: Underlying symbol (e.g., 'NIFTY', 'BANKNIFTY', 'NIFTY28OCT25FUT')
        exchange: Exchange for underlying ('NSE_INDEX', 'BSE_INDEX', 'NFO')
        legs: List of leg dictionaries (1-20 legs). Each leg must contain:
            Required:
            - offset: Strike offset ('ATM', 'ITM1'-'ITM50', 'OTM1'-'OTM50')
            - symbol: Explicit NFO option symbol (e.g. NIFTY24JUL2524500CE) — use instead of offset when closing legs
            - option_type: 'CE' for Call or 'PE' for Put
            - action: 'BUY' or 'SELL'
            - quantity: Absolute quantity — must be a multiple of the contract lot size.
                        Do NOT hardcode lot size. Look up the current 'lotsize' per leg
                        using get_option_symbol() or get_option_chain() first, then pass
                        quantity = lots * lotsize. Lot sizes can change (e.g., NIFTY has
                        changed multiple times) and differ by underlying.
            Optional:
            - expiry_date: Per-leg expiry in DDMMMYY format for diagonal/calendar spreads
            - pricetype: 'MARKET', 'LIMIT', 'SL', 'SL-M' (default: MARKET)
            - product: 'MIS', 'NRML' (default: MIS)
            - price: Limit price for LIMIT orders
            - trigger_price: Trigger price for SL orders
            - disclosed_quantity: Disclosed quantity
        expiry_date: Default expiry date in format 'DDMMMYY' (e.g., '25NOV25') for all legs

    Returns:
        JSON with underlying, underlying_ltp, mode, and results array containing each leg's
        orderid, symbol, offset, option_type, action, and status

    Example - Iron Condor (same expiry):
        [
            {"offset": "OTM10", "option_type": "CE", "action": "BUY", "quantity": 75},
            {"offset": "OTM10", "option_type": "PE", "action": "BUY", "quantity": 75},
            {"offset": "OTM5", "option_type": "CE", "action": "SELL", "quantity": 75},
            {"offset": "OTM5", "option_type": "PE", "action": "SELL", "quantity": 75}
        ]

    Example - Bull Call Spread with NRML:
        [
            {"offset": "ATM", "option_type": "CE", "action": "BUY", "quantity": 75, "product": "NRML"},
            {"offset": "OTM1", "option_type": "CE", "action": "SELL", "quantity": 75, "product": "NRML"}
        ]

    Example - Diagonal Spread (different expiry):
        [
            {"offset": "ITM2", "option_type": "CE", "action": "BUY", "quantity": 75, "expiry_date": "30DEC25"},
            {"offset": "OTM2", "option_type": "CE", "action": "SELL", "quantity": 75, "expiry_date": "25NOV25"}
        ]

    Example - Long Straddle with LIMIT orders:
        [
            {"offset": "ATM", "option_type": "CE", "action": "BUY", "quantity": 30, "pricetype": "LIMIT", "price": 250.0},
            {"offset": "ATM", "option_type": "PE", "action": "BUY", "quantity": 30, "pricetype": "LIMIT", "price": 250.0}
        ]
    """
    try:
        params = {
            "strategy": strategy,
            "underlying": underlying.upper(),
            "exchange": exchange.upper(),
            "legs": legs,
        }

        if expiry_date is not None:
            params["expiry_date"] = expiry_date

        response = client.optionsmultiorder(**params)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error placing options multi order: {str(e)}"


@mcp.tool()
def modify_order(
    order_id: str,
    symbol: str,
    action: str,
    exchange: str,
    product: str,
    quantity: int,
    price: float,
    strategy: str = MCP_STRATEGY,
    price_type: str = "LIMIT",
    trigger_price: float = 0,
    disclosed_quantity: int = 0,
) -> str:
    """
    Modify an existing order.

    Args:
        order_id: Order ID to modify
        symbol: Stock symbol
        action: 'BUY' or 'SELL'
        exchange: Exchange name
        product: 'CNC', 'NRML', 'MIS'
        quantity: New quantity
        price: New price (required by the API — use current price if unchanged)
        strategy: Strategy name (defaults to 'python mcp')
        price_type: 'MARKET', 'LIMIT', 'SL', 'SL-M' (defaults to 'LIMIT')
        trigger_price: New trigger price for SL/SL-M orders (default 0)
        disclosed_quantity: New disclosed quantity (default 0)
    """
    try:
        response = client.modifyorder(
            order_id=order_id,
            strategy=strategy,
            symbol=symbol.upper(),
            action=action.upper(),
            exchange=exchange.upper(),
            price_type=price_type.upper(),
            product=product.upper(),
            quantity=quantity,
            price=price,
            trigger_price=trigger_price,
            disclosed_quantity=disclosed_quantity,
        )
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error modifying order: {str(e)}"


@mcp.tool()
def cancel_order(order_id: str, strategy: str = MCP_STRATEGY) -> str:
    """
    Cancel a specific order.

    Args:
        order_id: Order ID to cancel
        strategy: Strategy name (defaults to 'python mcp')
    """
    try:
        response = client.cancelorder(order_id=order_id, strategy=strategy)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error canceling order: {str(e)}"


@mcp.tool()
def cancel_all_orders(strategy: str = MCP_STRATEGY) -> str:
    """
    Cancel all open orders for a strategy.

    Args:
        strategy: Strategy name (defaults to 'python mcp')
    """
    try:
        response = client.cancelallorder(strategy=strategy)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error canceling all orders: {str(e)}"


# POSITION MANAGEMENT TOOLS


@mcp.tool()
def close_all_positions(strategy: str = MCP_STRATEGY) -> str:
    """
    Close all open positions for a strategy.

    Args:
        strategy: Strategy name (defaults to 'python mcp')
    """
    try:
        response = client.closeposition(strategy=strategy)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error closing positions: {str(e)}"


@mcp.tool()
def get_open_position(
    symbol: str, exchange: str, product: str, strategy: str = MCP_STRATEGY
) -> str:
    """
    Get current open position for a specific instrument.

    Args:
        symbol: Stock symbol
        exchange: Exchange name
        product: Product type ('CNC', 'NRML', 'MIS')
        strategy: Strategy name (defaults to 'python mcp')
    """
    try:
        response = client.openposition(
            strategy=strategy,
            symbol=symbol.upper(),
            exchange=exchange.upper(),
            product=product.upper(),
        )
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting open position: {str(e)}"


# ORDER STATUS AND TRACKING TOOLS


@mcp.tool()
def get_order_status(order_id: str, strategy: str = MCP_STRATEGY) -> str:
    """
    Get status of a specific order.

    Args:
        order_id: Order ID
        strategy: Strategy name (defaults to 'python mcp')
    """
    try:
        response = client.orderstatus(order_id=order_id, strategy=strategy)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting order status: {str(e)}"


@mcp.tool()
def get_order_book() -> str:
    """Get all orders from the order book."""
    try:
        response = client.orderbook()
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting order book: {str(e)}"


@mcp.tool()
def get_trade_book() -> str:
    """Get all executed trades."""
    try:
        response = client.tradebook()
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting trade book: {str(e)}"


@mcp.tool()
def get_position_book() -> str:
    """Get all current positions."""
    try:
        response = client.positionbook()
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting position book: {str(e)}"


@mcp.tool()
def get_holdings() -> str:
    """Get all holdings (long-term investments)."""
    try:
        response = client.holdings()
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting holdings: {str(e)}"


@mcp.tool()
def get_funds() -> str:
    """Get account funds and margin information."""
    try:
        response = client.funds()
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting funds: {str(e)}"


@mcp.tool()
def calculate_margin(positions: list[dict[str, Any]]) -> str:
    """
    Calculate margin requirements for positions.

    Args:
        positions: List of position dictionaries
        Example: [{"symbol": "NIFTY25NOV2525000CE", "exchange": "NFO", "action": "BUY", "product": "NRML", "pricetype": "MARKET", "quantity": "75"}]

        For Futures: [{"symbol": "NIFTY25NOV25FUT", "exchange": "NFO", "action": "BUY", "product": "NRML", "pricetype": "MARKET", "quantity": "25"}]
        For Options: [{"symbol": "NIFTY25NOV2525500CE", "exchange": "NFO", "action": "BUY", "product": "NRML", "pricetype": "MARKET", "quantity": "75"}]

    Returns:
        JSON with total_margin_required, span_margin, and exposure_margin
    """
    try:
        response = client.margin(positions=positions)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error calculating margin: {str(e)}"


# MARKET DATA TOOLS — hub channel (read-first + write-through), not pip SDK `client`


@mcp.tool()
def get_quote(symbol: str, exchange: str = "NSE") -> str:
    """
    Get current quote for a symbol.

    Args:
        symbol: Stock symbol
        exchange: Exchange name
    """
    try:
        _ensure_trade_stack_import()
        from trade_integrations.hub_capture.channel import get_quote as channel_get_quote
        from trade_integrations.openalgo.freshness import FreshnessPolicy
        from trade_integrations.openalgo.market_data import fetch_quote_raw

        quote = channel_get_quote(
            symbol,
            lambda sym: fetch_quote_raw(sym, exchange=exchange),
            policy=FreshnessPolicy.NORMAL,
        )
        return json.dumps(quote, indent=2, default=str)
    except Exception as e:
        return f"Error getting quote: {str(e)}"


@mcp.tool()
def get_multi_quotes(symbols: list[dict[str, str]]) -> str:
    """
    Get real-time quotes for multiple symbols in a single request.

    Args:
        symbols: List of symbol-exchange pairs
        Example: [{"symbol": "RELIANCE", "exchange": "NSE"}, {"symbol": "INFY", "exchange": "NSE"}]

    Returns:
        JSON with quotes for all requested symbols including ltp, bid, ask, open, high, low, volume, oi
    """
    try:
        _ensure_trade_stack_import()
        from trade_integrations.hub_capture.channel import get_multi_quotes as channel_get_multi_quotes
        from trade_integrations.openalgo.freshness import FreshnessPolicy
        from trade_integrations.openalgo.market_data import fetch_multi_quotes_raw

        normalized_symbols = [
            {"symbol": s["symbol"].upper(), "exchange": s["exchange"].upper()} for s in symbols
        ]
        quotes = channel_get_multi_quotes(
            normalized_symbols,
            fetch_multi_quotes_raw,
            policy=FreshnessPolicy.NORMAL,
        )
        return json.dumps(quotes, indent=2, default=str)
    except Exception as e:
        return f"Error getting multi quotes: {str(e)}"


@mcp.tool()
def get_us_quote(symbol: str) -> str:
    """
    Get a near-real-time US equity quote via Alpaca paper/live data API.

    Requires ALPACA_API_KEY and ALPACA_API_SECRET in the trade stack .env.
    Uses IEX feed by default (ALPACA_DATA_FEED=iex).

    Args:
        symbol: US ticker (e.g. AAPL, MSFT, BRK-B)

    Returns:
        JSON with ltp, bid, ask, feed, and profile (paper/live).
    """
    try:
        from trade_integrations.dataflows.alpaca import (
            alpaca_configured,
            fetch_alpaca_quote,
            fetch_alpaca_trade_snapshot,
        )

        if not alpaca_configured():
            return (
                "Error: Alpaca is not configured. Set ALPACA_API_KEY and "
                "ALPACA_API_SECRET in the trade stack .env file."
            )
        clean = symbol.strip().upper()
        quote = fetch_alpaca_quote(clean)
        if quote and quote.get("ltp") is not None:
            return json.dumps(quote, indent=2, default=str)
        snap = fetch_alpaca_trade_snapshot(clean)
        if snap:
            return json.dumps(snap, indent=2, default=str)
        return f"Error: no Alpaca quote available for {clean}"
    except Exception as e:
        return f"Error getting US quote: {str(e)}"


@mcp.tool()
def get_us_paper_account() -> str:
    """
    Fetch Alpaca paper trading account summary (cash, equity, buying power).

    Requires ALPACA_API_KEY / ALPACA_API_SECRET with ALPACA_PROFILE=paper.
    """
    try:
        from trade_integrations.dataflows.alpaca import alpaca_configured, fetch_alpaca_account

        if not alpaca_configured():
            return (
                "Error: Alpaca is not configured. Set ALPACA_API_KEY and "
                "ALPACA_API_SECRET in the trade stack .env file."
            )
        return json.dumps(fetch_alpaca_account(), indent=2, default=str)
    except Exception as e:
        return f"Error getting Alpaca account: {str(e)}"


@mcp.tool()
def get_option_chain(
    underlying: str,
    exchange: str,
    expiry_date: str | None = None,
    strike_count: int | None = None,
) -> str:
    """
    Get option chain data with real-time quotes for all strikes.

    Args:
        underlying: Underlying symbol (e.g., 'NIFTY', 'BANKNIFTY', 'RELIANCE',
                    or a future like 'NIFTY30DEC25FUT')
        exchange: Exchange for underlying ('NSE_INDEX', 'BSE_INDEX', 'NSE', 'BSE', 'NFO', 'BFO')
        expiry_date: Expiry date in DDMMMYY format (e.g., '30DEC25'). Optional when the
                     underlying already includes an expiry (e.g., 'NIFTY30DEC25FUT').
        strike_count: Number of strikes above and below ATM (1-100). If not provided, returns entire chain.

    Returns:
        JSON with:
        - underlying: Base symbol
        - underlying_ltp: Current price of underlying
        - expiry_date: Expiry date
        - atm_strike: At-The-Money strike price
        - chain: Array of strikes with CE and PE data including:
            - symbol, label (ATM/ITM1/OTM1 etc.), ltp, bid, ask, open, high, low, volume, oi, lotsize

    Note: CE and PE have different labels at the same strike:
        - Strikes below ATM: CE is ITM, PE is OTM
        - Strikes above ATM: CE is OTM, PE is ITM

    Example for 10 strikes around ATM:
        get_option_chain("NIFTY", "NSE_INDEX", "30DEC25", 10)

    Example for full chain:
        get_option_chain("NIFTY", "NSE_INDEX", "30DEC25")

    Uses hub cache when entity registered; pass refresh via env LIVE if needed later.
    """
    try:
        chain_snapshot = _chain_snapshot_via_hub_channel(
            underlying,
            exchange,
            expiry_date=_normalize_openalgo_expiry(expiry_date) if expiry_date else None,
            strike_count=strike_count,
        )
        return json.dumps(chain_snapshot, indent=2, default=str)
    except Exception as e:
        return f"Error getting option chain: {str(e)}"


def _ensure_trade_stack_import() -> None:
    """Prepare sys.path and skip TradingAgents graph patches for MCP tools."""
    from pathlib import Path

    os.environ.setdefault("TRADE_INTEGRATIONS_SKIP_APPLY", "1")
    trade_root = Path(__file__).resolve().parents[2]
    integrations = trade_root / "integrations"
    tradingagents = trade_root / "tradingagents"
    for path in (integrations, tradingagents):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _import_payoff_charges():
    """Load trade-stack payoff/charges helpers when the repo is co-located."""
    _ensure_trade_stack_import()
    from trade_integrations.dataflows.options_research.payoff_charges import (
        calculate_charges,
        compute_payoff,
        estimate_strategy_metrics,
    )

    return compute_payoff, calculate_charges, estimate_strategy_metrics


def _import_options_research():
    """Load trade-stack options browse + plan helpers when co-located."""
    _ensure_trade_stack_import()
    from trade_integrations.dataflows.options_research.browse_summary import (
        build_browse_summary,
        format_browse_markdown,
    )
    from trade_integrations.tools.options_research_tools import fetch_options_research_report

    return build_browse_summary, format_browse_markdown, fetch_options_research_report


def _import_module_from_file(module_name: str, file_path: str):
    """Import a single .py file without triggering trade_integrations package init."""
    import importlib.util
    from pathlib import Path

    path = Path(file_path)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_browse_summary_lightweight():
    """Browse helpers without pulling the full trade_integrations stack."""
    from pathlib import Path

    trade_root = Path(__file__).resolve().parents[2]
    browse_path = trade_root / "integrations/trade_integrations/dataflows/options_research/browse_summary.py"
    mod = _import_module_from_file("_oa_browse_summary", str(browse_path))
    return mod.build_browse_summary, mod.format_browse_markdown


def _import_json_safe_lightweight():
    from pathlib import Path

    trade_root = Path(__file__).resolve().parents[2]
    path = trade_root / "integrations/trade_integrations/dataflows/json_safe.py"
    mod = _import_module_from_file("_oa_json_safe", str(path))
    return mod.json_safe


def _normalize_openalgo_expiry(expiry: str) -> str:
    return expiry.strip().upper().replace("-", "")


def _unwrap_optionchain_response(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    if isinstance(data, dict) and data.get("chain"):
        return data
    if response.get("chain"):
        return response
    if isinstance(data, list):
        return {"chain": data}
    return data if isinstance(data, dict) else {}


def _chain_snapshot_via_hub_channel(
    underlying: str,
    exchange: str,
    *,
    expiry_date: str | None = None,
    strike_count: int | None = None,
) -> dict[str, Any]:
    """Build chain snapshot through hub channel (read-first + write-through)."""
    _ensure_trade_stack_import()
    from trade_integrations.dataflows.openalgo import _fetch_option_chain_raw
    from trade_integrations.hub_capture.channel import get_chain

    return get_chain(
        underlying,
        exchange,
        _fetch_option_chain_raw,
        expiry_date=expiry_date,
        strike_count=strike_count,
    )


def _chain_snapshot_from_optionchain(
    underlying: str,
    exchange: str,
    *,
    expiry_date: str | None = None,
    strike_count: int | None = None,
) -> dict[str, Any]:
    """Build normalized chain snapshot via OpenAlgo SDK (no trade_integrations import)."""
    params: dict[str, Any] = {
        "underlying": underlying.upper(),
        "exchange": exchange.upper(),
    }
    if expiry_date:
        params["expiry_date"] = _normalize_openalgo_expiry(expiry_date)
    if strike_count is not None:
        params["strike_count"] = strike_count
    response = client.optionchain(**params)
    meta = _unwrap_optionchain_response(response if isinstance(response, dict) else {})
    chain = meta.get("chain") or []
    ce_oi = sum(int(row.get("ce", {}).get("oi") or 0) for row in chain if isinstance(row, dict))
    pe_oi = sum(int(row.get("pe", {}).get("oi") or 0) for row in chain if isinstance(row, dict))
    pcr = round(pe_oi / ce_oi, 4) if ce_oi else None
    return {
        "underlying": meta.get("underlying") or underlying.upper(),
        "underlying_ltp": meta.get("underlying_ltp"),
        "expiry_date": meta.get("expiry_date") or params.get("expiry_date"),
        "atm_strike": meta.get("atm_strike"),
        "chain": chain,
        "pcr": pcr,
        "total_call_oi": ce_oi,
        "total_put_oi": pe_oi,
        "source": "openalgo",
    }


def _fetch_expiries_via_client(underlying: str, options_exchange: str) -> list[str]:
    response = client.expiry(
        symbol=underlying.upper(),
        exchange=options_exchange.upper(),
        instrumenttype="options",
    )
    if not isinstance(response, dict):
        return []
    data = response.get("data") or {}
    if isinstance(data, list):
        return [str(x) for x in data]
    return [str(x) for x in (data.get("expiry_dates") or data.get("expiries") or [])]


def _import_stock_research():
    _ensure_trade_stack_import()
    from trade_integrations.dataflows.stock_research.browse_summary import (
        build_stock_browse_summary,
        format_stock_browse_markdown,
    )
    from trade_integrations.dataflows.stock_research.aggregator import run_stock_research
    from trade_integrations.dataflows.stock_research.format import format_stock_report
    from trade_integrations.context.hub import load_stock_research_json, save_stock_research

    return (
        build_stock_browse_summary,
        format_stock_browse_markdown,
        run_stock_research,
        format_stock_report,
        load_stock_research_json,
        save_stock_research,
    )


def _import_index_research():
    _ensure_trade_stack_import()
    from trade_integrations.context.hub import load_index_research_json, save_index_research
    from trade_integrations.dataflows.index_research.aggregator import run_index_research
    from trade_integrations.dataflows.index_research.format import format_index_report
    from trade_integrations.tools.index_research_tools import fetch_index_research_report

    return (
        run_index_research,
        format_index_report,
        load_index_research_json,
        save_index_research,
        fetch_index_research_report,
    )


@mcp.tool()
def get_strategy_payoff(
    legs: list[dict[str, Any]],
    spot: float,
    range_pct: float = 0.12,
    steps: int = 80,
    expiry_date: str | None = None,
    iv: float | None = None,
) -> str:
    """
    Compute expiry payoff curve for a multi-leg options strategy.

    Args:
        legs: Strategy legs with side (BUY/SELL), strike, option_type (CE/PE),
              price, quantity (or lot_size * lots), symbol optional.
        spot: Current underlying price for sampling range.
        range_pct: Underlying range as fraction of spot (default 12%).
        steps: Number of payoff samples.

        expiry_date: Optional expiry DDMMMYY for OptionLab PoP.
        iv: Optional ATM IV (percent) for OptionLab PoP.

    Returns:
        JSON with samples, breakevens, max_profit, max_loss, pop, and net P&L fields.
    """
    try:
        _, _, estimate_strategy_metrics = _import_payoff_charges()
        result = estimate_strategy_metrics(
            legs,
            spot=spot,
            expiry=expiry_date,
            iv=iv,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error computing strategy payoff: {str(e)}"


@mcp.tool()
def get_trade_charges(
    legs: list[dict[str, Any]],
    broker_preset: str | None = None,
) -> str:
    """
    Estimate India F&O charges per leg and portfolio total.

    Args:
        legs: Strategy legs with side, price, quantity (or lot_size * lots).
        broker_preset: Charge model preset (default: OpenAlgo session / indmoney).

    Returns:
        JSON with per_leg breakdown and total (brokerage, STT, GST, stamp, exchange).
    """
    try:
        from trade_integrations.research.broker_context import resolve_broker_preset

        _, calculate_charges, _ = _import_payoff_charges()
        broker = broker_preset or resolve_broker_preset()
        result = calculate_charges(legs, broker_preset=broker)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error calculating trade charges: {str(e)}"


@mcp.tool()
def get_options_browse(
    underlying: str,
    exchange: str,
    expiry_date: str | None = None,
    strike_count: int = 10,
) -> str:
    """
    Compact in-chat browse of a live India options chain (expiries, ATM, top strikes).

    Use this first when the user asks what options are available before loading a full trade plan.

    Args:
        underlying: Index or stock symbol (e.g. NIFTY, RELIANCE)
        exchange: NSE_INDEX, BSE_INDEX, NSE, or BSE
        expiry_date: Optional expiry DDMMMYY
        strike_count: Strikes above/below ATM (default 10)

    Returns:
        JSON with browse_summary and markdown (table for chat).
    """
    try:
        build_browse_summary, format_browse_markdown = _import_browse_summary_lightweight()
        json_safe = _import_json_safe_lightweight()

        chain_snapshot = _chain_snapshot_via_hub_channel(
            underlying,
            exchange,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )
        if not chain_snapshot.get("expiry_date"):
            options_exchange = "NFO" if exchange.upper() in ("NSE", "NSE_INDEX") else "BFO"
            expiries = _fetch_expiries_via_client(underlying, options_exchange)
            if expiries and not expiry_date:
                chain_snapshot = _chain_snapshot_via_hub_channel(
                    underlying,
                    exchange,
                    expiry_date=_normalize_openalgo_expiry(expiries[0]),
                    strike_count=strike_count,
                )
        options_exchange = "NFO" if exchange.upper() in ("NSE", "NSE_INDEX") else "BFO"
        expiries = _fetch_expiries_via_client(underlying, options_exchange)
        chain_snapshot["expiries"] = [_normalize_openalgo_expiry(e) for e in expiries]

        summary = build_browse_summary(chain_snapshot)
        payload = {
            "browse_summary": json_safe(summary),
            "markdown": format_browse_markdown(summary),
        }
        return json.dumps(payload, indent=2, default=str)
    except Exception as e:
        return f"Error browsing options chain: {str(e)}"


@mcp.tool()
def get_options_trade_plan(
    ticker: str,
    refresh: bool = False,
    expiry_date: str | None = None,
    lookahead_days: int | None = None,
) -> str:
    """
    Load or generate the full options trade plan from the trade-stack hub.

    Includes prediction, events, ranked strategies, recommended legs, payoff,
    charges, and implementation steps. Set refresh=true to bypass cache.

    Args:
        ticker: Underlying (NIFTY, BANKNIFTY, RELIANCE, …)
        refresh: When true, regenerate even if hub cache is fresh
        expiry_date: Optional expiry DDMMMYY
        lookahead_days: Event lookahead window (default from env)

    Returns:
        Markdown trade plan ready for agent explanation.
    """
    try:
        _, _, fetch_options_research_report = _import_options_research()
        report = fetch_options_research_report(
            ticker,
            expiry_date=expiry_date,
            lookahead_days=lookahead_days,
            use_cache=not refresh,
        )
        return report
    except Exception as e:
        return f"Error loading options trade plan: {str(e)}"


def _trade_widget_store_dir():
    from pathlib import Path

    root = Path.home() / ".vibe-trading" / "trade_widgets"
    root.mkdir(parents=True, exist_ok=True)
    return root


@mcp.tool()
def get_options_trade_widget(
    ticker: str,
    refresh: bool = False,
    expiry_date: str | None = None,
    lookahead_days: int | None = None,
) -> str:
    """
    Build a structured trade-plan widget for Vibe chat (scenarios, payoff chart data,
    charges, recommended legs, execute steps).

    Call when presenting ranked strategy options or a recommended plan with legs — not for
    browse-only, prediction-only, or event summaries without actionable strategies.

    Returns JSON with type ``trade_plan.widget``. The Vibe UI renders this as an
    interactive card with payoff graph and Execute button.

    Args:
        ticker: Underlying (NIFTY, RELIANCE, AAPL, …)
        refresh: Regenerate hub plan before building widget
        expiry_date: Optional expiry DDMMMYY
        lookahead_days: Event lookahead window

    Returns:
        JSON widget payload (also persisted under ~/.vibe-trading/trade_widgets/).
    """
    try:
        _ensure_trade_stack_import()
        from trade_integrations.dataflows.options_research.widget_payload import (
            build_options_trade_widget,
        )

        widget = build_options_trade_widget(
            ticker,
            expiry_date=expiry_date,
            lookahead_days=lookahead_days,
            refresh=refresh,
        )
        widget_id = widget.get("widget_id")
        if widget_id:
            store = _trade_widget_store_dir() / f"{widget_id}.json"
            store.write_text(json.dumps(widget, indent=2, default=str), encoding="utf-8")
        return json.dumps(widget, indent=2, default=str)
    except Exception as e:
        return json.dumps(
            {"type": "trade_plan.widget", "error": str(e), "underlying": ticker},
            indent=2,
        )


@mcp.tool()
def get_plan_position_status(widget_id: str) -> str:
    """
    Return execution ledger entry and matched broker positions for a trade widget.

    Gated by OPTIONS_REALTIME_MONITOR_ENABLED for legacy paths; always returns
    ledger + thesis-break when a ledger entry exists (for auto paper trading).

    Args:
        widget_id: Persisted trade-plan widget id (tp_*)

    Returns:
        JSON with ledger entry, matched positions, thesis-break report, and position P&L.
    """
    try:
        _ensure_trade_stack_import()
        from trade_integrations.monitor.config import is_monitor_enabled
        from trade_integrations.context.hub import load_options_research_json
        from trade_integrations.monitor.execution_ledger import (
            fetch_position_book,
            get_ledger_entry,
            match_positions_for_entry,
        )
        from trade_integrations.monitor.live_quotes import fetch_underlying_ltp
        from trade_integrations.monitor.thesis_break import evaluate_thesis_break

        ledger_entry = get_ledger_entry(widget_id)
        if ledger_entry is None:
            return json.dumps({"widget_id": widget_id, "ledger": None})

        position_book = fetch_position_book()
        matched_positions, position_pnl = match_positions_for_entry(
            ledger_entry,
            position_book or {},
        )
        underlying = str(ledger_entry.get("underlying") or "").strip().upper()
        doc = load_options_research_json(underlying) if underlying else None
        live_spot = fetch_underlying_ltp(underlying) if underlying else None
        thesis_report = evaluate_thesis_break(
            doc,
            ledger_entry,
            live_spot=live_spot,
            position_pnl=position_pnl,
        )
        payload: dict[str, Any] = {
            "widget_id": widget_id,
            "ledger": ledger_entry,
            "matched_positions": matched_positions,
            "position_pnl": position_pnl,
            "monitor_enabled": is_monitor_enabled(),
        }
        payload["thesis_break"] = {
            "broken": thesis_report.broken,
            "reasons": thesis_report.reasons,
            "severity": thesis_report.severity,
            "live_spot": thesis_report.live_spot,
            "plan_spot": thesis_report.plan_spot,
            "position_pnl": thesis_report.position_pnl,
        }
        return json.dumps(payload, indent=2, default=str)
    except Exception as e:
        return json.dumps({"widget_id": widget_id, "error": str(e)}, indent=2)


def _import_auto_paper():
    _ensure_trade_stack_import()
    from trade_integrations.auto_paper import mcp_actions

    return mcp_actions


@mcp.tool()
def start_auto_paper_trading(
    ticker: str,
    budget_inr: float = 20000.0,
    watchlist: list[str] | None = None,
    max_daily_loss_inr: float = 2000.0,
    goal: str | None = None,
    mandate: str | None = None,
    vibe_session_id: str | None = None,
) -> str:
    """
    Start **autonomous** intraday paper trading — no human confirmation per order.

    Enables OpenAlgo analyzer mode, saves mandate, starts scheduler agent turns
    (when VIBE_TRADING_ENABLE_SCHEDULER=1), and returns initial market feedback.
    After starting, immediately research and act in the same turn without asking user.

    Args:
        ticker: Primary underlying (e.g. NIFTY, BANKNIFTY)
        budget_inr: Max paper capital to deploy (default 20000)
        watchlist: Optional list of symbols to rotate; defaults to [ticker]
        max_daily_loss_inr: Halt new entries after this daily loss (default 2000)
        goal: Profit objective in plain language
        mandate: Full user mandate to persist for scheduler turns
        vibe_session_id: Vibe chat session id for scheduler continuity (auto-injected when called from Vibe)

    Returns:
        JSON with session status and recommended next MCP calls.
    """
    try:
        actions = _import_auto_paper()
        result = actions.start_auto_paper(
            ticker=ticker,
            budget_inr=budget_inr,
            watchlist=watchlist,
            max_daily_loss_inr=max_daily_loss_inr,
            goal=goal,
            mandate=mandate,
            agent_mode=True,
            vibe_session_id=vibe_session_id,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def stop_auto_paper_trading() -> str:
    """
    Stop the active intraday paper trading session and remove its scheduler cron job.

    Use when the user asks to stop auto trading, square off for the day, or
    end the paper session. Unregisters the auto-paper-agent-turn cron from
    Vibe scheduler so no further background turns run. Does not close open
    positions — call close_all_positions first if flat is desired.

    Returns:
        JSON confirmation.
    """
    try:
        actions = _import_auto_paper()
        return json.dumps(actions.stop_auto_paper(), indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def resume_auto_paper_trading(
    vibe_session_id: str | None = None,
) -> str:
    """
    Resume an interrupted autonomous paper trading session after crash or API restart.

    Returns session state, market feedback, and a resume_prompt to send to the Vibe
    agent (or rely on POST /trade/auto-paper/resume?dispatch=true).

    Args:
        vibe_session_id: Optional Vibe chat session to bind for continuity

    Returns:
        JSON with resume_prompt and session status.
    """
    try:
        actions = _import_auto_paper()
        result = actions.resume_auto_paper(vibe_session_id=vibe_session_id)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def get_auto_paper_status() -> str:
    """
    Get active intraday paper trading session state.

    Call at the **start of every trading turn** and when the user asks how the
    session is going. Returns budget, open positions, sandbox funds, P&L,
    market hours, last decisions, and halt state.

    Returns:
        JSON session summary for agent decisions.
    """
    try:
        actions = _import_auto_paper()
        return json.dumps(actions.get_status(), indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def get_auto_paper_market_feedback(ticker: str | None = None) -> str:
    """
    Live market feedback for the autonomous paper trader.

    Returns spot vs plan drift, material news, open position P&L, thesis-break
    alerts, and deltas since the last agent turn. Call at the start of every
    autonomous turn and whenever the market may have changed.

    Args:
        ticker: Optional focus underlying; defaults to session primary ticker

    Returns:
        JSON with alerts, summary, tickers, open_positions, deltas_since_last_turn
    """
    try:
        actions = _import_auto_paper()
        result = actions.get_market_feedback(ticker=ticker)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def execute_auto_paper_basket(widget_id: str) -> str:
    """
    Execute a researched trade-plan widget in **paper** mode (OpenAlgo sandbox).

    Call after get_options_trade_widget + margin/charges validation when you
    decide to ENTER. Forces analyzer mode, places basket orders from widget
    implementation_steps, and records to the execution ledger.

    Args:
        widget_id: Trade plan widget id (tp_*)

    Returns:
        JSON with execution results.
    """
    try:
        actions = _import_auto_paper()
        result = actions.execute_basket(widget_id)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def record_auto_paper_decision(
    decision: str,
    rationale: str,
    ticker: str | None = None,
    actions_taken: list[str] | None = None,
) -> str:
    """
    Log the agent's trading decision for the current paper session turn.

    Call at the end of every active trading turn with ENTER, EXIT, HOLD, or SKIP
    plus clear rationale (why this maximizes profit or limits loss).

    Args:
        decision: ENTER | EXIT | HOLD | SKIP
        rationale: Why this action (research, spot move, thesis, budget)
        ticker: Underlying symbol
        actions_taken: MCP tools called this turn (e.g. get_options_trade_plan)

    Returns:
        JSON confirmation.
    """
    try:
        actions = _import_auto_paper()
        result = actions.record_decision(
            decision=decision,
            rationale=rationale,
            ticker=ticker,
            actions_taken=actions_taken,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


def _import_autonomous_agents():
    _ensure_trade_stack_import()
    from trade_integrations.autonomous_agents import mcp_actions

    return mcp_actions


@mcp.tool()
def propose_autonomous_agent(
    symbols: list[str],
    name: str | None = None,
    mandate: str | None = None,
    budget_inr: float | None = None,
    max_daily_loss_inr: float | None = None,
    confidence_threshold: int | None = None,
    watch_interval_min: int | None = None,
    research_interval_min: int | None = None,
    mode: str = "paper",
    execution_market: str | None = None,
    user_text: str | None = None,
    allowed_instruments: list[str] | None = None,
    vibe_session_id: str | None = None,
) -> str:
    """
    Propose a persistent autonomous trading agent (read-only — user confirms in UI).

    Creates a proposal card; does NOT start the agent until the user clicks Confirm.

    Args:
        symbols: Symbols to watch/trade (e.g. ["NIFTY"])
        name: Display name
        mandate: Trading goal / constraints in plain language
        budget_inr: Paper budget (default 20000)
        max_daily_loss_inr: Daily loss halt (default 2000)
        confidence_threshold: Act when confidence >= this (default 75)
        watch_interval_min: News/market watch cadence (default 7 min)
        research_interval_min: Full reasoning cadence (default 90 min)
        mode: paper only in v1
        execution_market: Optional IN or US override when user explicitly chose market
        user_text: Original user message for market hint resolution
        allowed_instruments: equity and/or options — omit to auto-infer (RELIANCE defaults equity)
        vibe_session_id: Orchestrator chat session id

    Returns:
        JSON with status, proposal_id, missing_fields, and proposal when ready.
    """
    try:
        actions = _import_autonomous_agents()
        result = actions.mcp_propose(
            symbols=symbols,
            name=name,
            mandate=mandate,
            budget_inr=budget_inr,
            max_daily_loss_inr=max_daily_loss_inr,
            confidence_threshold=confidence_threshold,
            watch_interval_min=watch_interval_min,
            research_interval_min=research_interval_min,
            mode=mode,
            execution_market=execution_market,
            user_text=user_text,
            allowed_instruments=allowed_instruments,
            orchestrator_session_id=vibe_session_id,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def get_autonomous_agent_status(agent_id: str | None = None) -> str:
    """
    Get status of one autonomous agent or list all agents.

    Args:
        agent_id: Optional aa_* id; omit to list all

    Returns:
        JSON agent state or agent list.
    """
    try:
        actions = _import_autonomous_agents()
        result = actions.mcp_get_status(agent_id=agent_id)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def record_autonomous_decision(
    agent_id: str,
    decision: str,
    rationale: str,
    ticker: str | None = None,
    actions_taken: list[str] | None = None,
    confidence: int | None = None,
    direction: str | None = None,
    strategy: str | None = None,
) -> str:
    """
    Log an autonomous agent decision (ENTER/REVISE/EXIT/HOLD/SKIP).

    Wraps record_auto_paper_decision and updates the agent instance thesis
    (direction, strategy, confidence, rationale).
    """
    try:
        actions = _import_autonomous_agents()
        result = actions.mcp_record_decision(
            agent_id=agent_id,
            decision=decision,
            rationale=rationale,
            ticker=ticker,
            actions_taken=actions_taken,
            confidence=confidence,
            direction=direction,
            strategy=strategy,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def set_agent_watch_spec(
    agent_id: str,
    watch_spec: dict | None = None,
    strategy: str | None = None,
) -> str:
    """
    Persist Nautilus-compatible watch rules on an autonomous agent instance.

    Prefer `strategy` — backend derives rules from the chosen strategy (hold_cash,
    buy_dip, momentum_breakout, etc.). Or pass explicit `watch_spec`.

    Args:
        agent_id: aa_* agent id
        watch_spec: optional explicit {rules: [...], gate: {...}, cooldown_sec: 300}
        strategy: recommended strategy name — rules derived automatically
    """
    try:
        actions = _import_autonomous_agents()
        result = actions.mcp_set_watch_spec(
            agent_id=agent_id,
            watch_spec=watch_spec,
            strategy=strategy,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def get_quant_monitor_status(agent_id: str) -> str:
    """
    Quant monitor snapshot for an autonomous agent (profile, baselines, last alert).

    Args:
        agent_id: aa_* agent id

    Returns:
        JSON with quant_state and last_quant_alert_at.
    """
    try:
        actions = _import_autonomous_agents()
        result = actions.mcp_get_quant_monitor_status(agent_id=agent_id)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def submit_bridge_execution_intent(
    agent_id: str,
    action: str,
    rationale: str,
    widget_id: str | None = None,
    underlying: str | None = None,
) -> str:
    """
    Submit an execution intent for India autonomous agents (bridge → OpenAlgo).

    Use for EXIT/ADJUST when not using execute_auto_paper_basket.
    ENTER with legs typically goes through execute_auto_paper_basket instead.
    """
    try:
        actions = _import_autonomous_agents()
        result = actions.mcp_submit_bridge_execution_intent(
            agent_id=agent_id,
            action=action,
            rationale=rationale,
            widget_id=widget_id,
            underlying=underlying,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def get_research_status(
    ticker: str,
    asset_type: str = "stock",
) -> str:
    """
    Check unified research pipeline stage completion for a ticker.

    Args:
        ticker: Symbol (RELIANCE, NIFTY, …)
        asset_type: stock, options, or index

    Returns:
        JSON with status, stages checklist, missing fields, debate_pending.
    """
    try:
        from trade_integrations.research.orchestrator import get_research_status as _status
        from trade_integrations.research.registry import ResearchKind

        kind_map = {
            "stock": ResearchKind.STOCK,
            "options": ResearchKind.OPTIONS,
            "index": ResearchKind.INDEX,
        }
        kind = kind_map.get(asset_type.strip().lower(), ResearchKind.STOCK)
        return json.dumps(_status(ticker, kind=kind), indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def get_nse_browser_status() -> str:
    """
    Read hub status for NSE/NSDL browser datasets (nodriver module).

    Returns JSON with per-dataset row counts, freshness, last mission status, and agent config.
    Does not refresh data — use get_nse_browser_data to fetch and return rows.
    """
    try:
        from trade_integrations.tools.nse_browser_tools import query_nse_browser_status as _status

        return _status()
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def get_nse_browser_data(
    dataset: str = "fii_dii",
    start_date: str | None = None,
    end_date: str | None = None,
    refresh: bool = False,
    refresh_cookies: bool = False,
    agent_fallback: bool = True,
    backfill_historical: bool = False,
    limit: int = 500,
) -> str:
    """
    Fetch NSE/NSDL data not available via simple APIs (primary agent tool).

    Reads hub cache first; browses NSE/NSDL via nodriver only when stale or refresh=True.
    Returns parsed rows in JSON and persists to hub parquet under reports/hub/_data/nse_browser/.

    Agent routing:
    - FII/DII / institutional / fiidii flows → dataset=\"fii_dii\"
    - FPI / NSDL foreign portfolio → dataset=\"fpi\"
    - Bulk or block deals → dataset=\"bulk_deals\"
    - Delivery position → dataset=\"delivery\"
    - Index PE/PB → dataset=\"pe_pb\"

    Args:
        dataset: fii_dii | fpi | bulk_deals | delivery | pe_pb (aliases accepted: fii, dii, nsdl)
        start_date: YYYY-MM-DD (default ~30 days ago)
        end_date: YYYY-MM-DD (default today)
        refresh: Force live browser fetch even if cache is fresh
        refresh_cookies: Bootstrap nodriver session before fetch
        agent_fallback: MiniMax browser operator when navigation fails
        backfill_historical: Full historical CSV/archives backfill (~120s, headed browser)
        limit: Max rows returned

    Returns:
        JSON with status, records[], summary, freshness, hub_paths, mission_result.
    """
    try:
        from trade_integrations.tools.nse_browser_tools import query_nse_browser_data as _get

        return _get(
            dataset,
            start_date=start_date,
            end_date=end_date,
            refresh=refresh,
            refresh_cookies=refresh_cookies,
            agent_fallback=agent_fallback,
            backfill_historical=backfill_historical,
            limit=limit,
        )
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def ingest_nse_repository() -> str:
    """
    Sync git-tracked data/nse parquet into hub without browser fetch.

    Use after cloning the repo or when data/nse/*.parquet was updated locally.
    """
    try:
        from trade_integrations.tools.nse_browser_tools import query_ingest_nse_repository as _ingest

        return _ingest()
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def run_nse_browser_mission(
    mission: str = "fii_dii_history",
    refresh_cookies: bool = False,
    agent_fallback: bool = False,
    backfill_historical: bool = False,
) -> str:
    """
    Low-level: run one NSE/NSDL browser mission by id (ops/debug).

    Prefer get_nse_browser_data for agent use — it returns parsed rows and handles cache freshness.

    Args:
        mission: fii_dii_history | fpi_nsdl | market_archives
        refresh_cookies: Bootstrap nodriver session cookies before fetch
        agent_fallback: MiniMax operator when deterministic navigation fails

    Returns:
        JSON mission result with status, rows, artifacts, and date_range.
    """
    try:
        from trade_integrations.tools.nse_browser_tools import fetch_nse_browser_data

        return fetch_nse_browser_data(
            mission,
            refresh=True,
            refresh_cookies=refresh_cookies,
            agent_fallback=agent_fallback,
            backfill_historical=backfill_historical,
        )
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def run_browser_task(
    goal: str,
    start_urls: str | None = None,
    output_schema: str | None = None,
    max_steps: int = 50,
    persist: bool = True,
) -> str:
    """
    Agentic web browse/extract via local nodriver + MiniMax (ad-hoc research).

    Use for events, filings, macro pages, or any public URL. For preset NSE/NSDL
    datasets (FII/DII, FPI, archives) prefer get_nse_browser_data.

    Args:
        goal: Natural-language objective (required)
        start_urls: JSON array of entry URLs, e.g. ["https://www.rbi.org.in/"]
        output_schema: JSON schema string for structured extraction
        max_steps: MiniMax operator step budget (1–20)
        persist: Save artifacts under reports/hub/_data/nse_browser/tasks/

    Returns:
        JSON with status, structured_output, task_id, hub_path, action_log.
    """
    try:
        from trade_integrations.tools.nse_browser_tools import query_run_browser_task as _run

        urls: list[str] | None = None
        if start_urls:
            parsed = json.loads(start_urls)
            if isinstance(parsed, list):
                urls = [str(u) for u in parsed]
            elif isinstance(parsed, str):
                urls = [parsed]
        schema: dict | None = None
        if output_schema:
            schema = json.loads(output_schema)
        return _run(goal, start_urls=urls, output_schema=schema, max_steps=max_steps, persist=persist)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


@mcp.tool()
def get_stock_trade_widget(
    ticker: str,
    refresh: bool = False,
    lookahead_days: int = 14,
) -> str:
    """
    Build a structured stock trade-plan widget for Vibe chat.

    Returns JSON with type ``trade_plan.widget`` (asset_type stock) including
    scenarios, charges, ranked approaches, and execute steps.

    Args:
        ticker: NSE equity symbol (RELIANCE, TCS, …)
        refresh: Regenerate hub plan before building widget
        lookahead_days: Event lookahead window

    Returns:
        JSON widget payload (persisted under ~/.vibe-trading/trade_widgets/).
    """
    try:
        from trade_integrations.dataflows.stock_research.widget_payload import (
            build_stock_trade_widget,
        )

        widget = build_stock_trade_widget(
            ticker,
            lookahead_days=lookahead_days,
            refresh=refresh,
        )
        widget_id = widget.get("widget_id")
        if widget_id:
            store = _trade_widget_store_dir() / f"{widget_id}.json"
            store.write_text(json.dumps(widget, indent=2, default=str), encoding="utf-8")
        return json.dumps(widget, indent=2, default=str)
    except Exception as e:
        return json.dumps(
            {"type": "trade_plan.widget", "error": str(e), "underlying": ticker, "asset_type": "stock"},
            indent=2,
        )


def _import_agent_debate():
    _ensure_trade_stack_import()
    import trade_integrations  # noqa: F401
    from trade_integrations.bridge.agent_debate import run_agent_debate
    from trade_integrations.context.hub import (
        is_agent_debate_cache_fresh,
        load_agent_debate_json,
    )

    return run_agent_debate, load_agent_debate_json, is_agent_debate_cache_fresh


@mcp.tool()
def run_tradingagents_analysis(
    ticker: str,
    asset_type: str = "stock",
    refresh: bool = False,
) -> str:
    """
    Run the TradingAgents multi-agent debate (bull/bear/risk) and save to hub.

    Use when the user finalizes a plan or asks for a second opinion from agents.
    Returns markdown summary; full JSON lives at reports/hub/{TICKER}/agent_debate/.

    Args:
        ticker: Symbol (NIFTY, RELIANCE, …)
        asset_type: stock or options context for prefetch
        refresh: Bypass cached debate when true

    Returns:
        Markdown debate summary with rating and key perspectives.
    """
    try:
        run_agent_debate, load_agent_debate_json, is_agent_debate_cache_fresh = _import_agent_debate()
        from trade_integrations.bridge.hub_context import infer_debate_asset_type

        key = ticker.strip().upper()
        resolved_asset = infer_debate_asset_type(key, asset_type if asset_type in ("options", "stock") else None)
        if not refresh:
            cached = load_agent_debate_json(key)
            if cached and is_agent_debate_cache_fresh(key):
                from trade_integrations.dataflows.agent_debate.format import format_agent_debate_report

                return format_agent_debate_report(cached)

        import threading

        if not refresh:
            stale = load_agent_debate_json(key)
            if stale:
                from trade_integrations.dataflows.agent_debate.format import format_agent_debate_report

                body = format_agent_debate_report(stale)
                body += (
                    f"\n\n---\n*Note: debate cache is stale; a fresh run was started in the "
                    f"background for {key}. Check the Vibe Research panel → Agent debate tab.*"
                )
            else:
                body = (
                    f"TradingAgents debate started for **{key}** in the background "
                    f"(typically 2–5 minutes).\n\n"
                    f"Results will be saved to `reports/hub/{key}/agent_debate/` and appear in "
                    f"the Vibe **Research → Agent debate** side panel.\n\n"
                    f"Call this tool again with `refresh=false` once complete to read the summary."
                )
        else:
            body = (
                f"Refreshing TradingAgents debate for **{key}** in the background.\n"
                f"Call again with `refresh=false` when the Research panel shows ready."
            )

        def _worker() -> None:
            try:
                run_agent_debate(key, asset_type=resolved_asset)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True, name=f"mcp-debate-{key}").start()
        return body
    except Exception as e:
        return f"Error running TradingAgents analysis: {str(e)}"


def _import_quant_review():
    _ensure_trade_stack_import()
    import trade_integrations  # noqa: F401
    from trade_integrations.bridge.quant_review import run_quant_review
    from trade_integrations.context.hub import (
        is_quant_review_cache_fresh,
        load_quant_review_json,
    )

    return run_quant_review, load_quant_review_json, is_quant_review_cache_fresh


@mcp.tool()
def run_quant_review(
    ticker: str = "NIFTY",
    horizon_days: int = 14,
    refresh: bool = False,
) -> str:
    """
    Run India Quant Reviewer — second opinion vs Ridge forecast (TA + flows + surprises).

    Saves to reports/hub/{TICKER}/quant_review/latest.json. Label as reviewer opinion,
    not the headline model forecast.

    Args:
        ticker: Index symbol (NIFTY, BANKNIFTY)
        horizon_days: Prediction horizon for profile selection
        refresh: Recompute even when cache is fresh

    Returns:
        JSON summary with surprises, disagreements, and TA consensus.
    """
    import json

    try:
        run_review, load_review, is_fresh = _import_quant_review()
        key = ticker.strip().upper()
        if not refresh:
            cached = load_review(key)
            if cached and is_fresh(key):
                return json.dumps(cached, indent=2, default=str)
        payload = run_review(key, horizon_days=horizon_days, save=True)
        return json.dumps(payload, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "ticker": ticker}, indent=2)


@mcp.tool()
def get_stock_browse(ticker: str) -> str:
    """
    Compact in-chat browse for an equity (price, sector, 52w range, peers).

    Args:
        ticker: NSE equity symbol (e.g. RELIANCE, TCS)

    Returns:
        JSON with browse_summary and markdown table for chat.
    """
    try:
        (
            build_stock_browse_summary,
            format_stock_browse_markdown,
            _,
            _,
            load_stock_research_json,
            _,
        ) = _import_stock_research()
        from trade_integrations.context.hub import load_company_research_json
        from trade_integrations.dataflows.market_quotes import fetch_live_quote

        sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
        doc = load_stock_research_json(sym)
        if doc and doc.browse_summary:
            summary = doc.browse_summary
        else:
            company = load_company_research_json(sym)
            identity = company.identity if company else {}
            quote = fetch_live_quote(sym)
            peers = company.peers if company else []
            summary = build_stock_browse_summary(
                ticker=sym,
                identity=identity,
                quote=quote,
                peers=peers,
            )
        return json.dumps(
            {"browse_summary": summary, "markdown": format_stock_browse_markdown(summary)},
            indent=2,
            default=str,
        )
    except Exception as e:
        return f"Error browsing stock: {str(e)}"


@mcp.tool()
def get_stock_trade_plan(ticker: str, refresh: bool = False, lookahead_days: int = 14) -> str:
    """
    Load or generate a stock trade plan from the trade-stack hub.

    Includes prediction, ranked approaches, recommended action, charges, and steps.

    Args:
        ticker: Equity symbol (RELIANCE, TCS, …)
        refresh: Regenerate even if cache exists
        lookahead_days: Event lookahead window

    Returns:
        Markdown stock trade plan.
    """
    try:
        _ensure_trade_stack_import()
        from trade_integrations.tools.stock_research_tools import fetch_stock_research_report

        return fetch_stock_research_report(
            ticker,
            lookahead_days=lookahead_days,
            use_cache=not refresh,
        )
    except Exception as e:
        return f"Error loading stock trade plan: {str(e)}"


@mcp.tool()
def get_index_trade_plan(
    ticker: str = "NIFTY",
    refresh: bool = False,
    horizon_days: int | None = None,
) -> str:
    """
    Load or generate an index trade plan from the trade-stack hub.

    Includes prediction range, constituent attribution, macro factors, regime,
    scenarios, and model accuracy metrics. Set refresh=true to bypass cache.

    Args:
        ticker: Index symbol (NIFTY, BANKNIFTY, …)
        refresh: Regenerate even if cache exists
        horizon_days: Prediction horizon in days (default from env, usually 14)

    Returns:
        JSON with index_research payload and markdown summary.
    """
    try:
        (
            _,
            format_index_report,
            load_index_research_json,
            _,
            fetch_index_research_report,
        ) = _import_index_research()
        sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
        if refresh:
            markdown = fetch_index_research_report(
                sym,
                horizon_days=horizon_days,
                use_cache=False,
            )
            doc = load_index_research_json(sym)
        else:
            doc = load_index_research_json(sym)
            if doc:
                markdown = format_index_report(doc)
            else:
                markdown = fetch_index_research_report(sym, horizon_days=horizon_days)
                doc = load_index_research_json(sym)
        payload = doc.to_dict() if doc and hasattr(doc, "to_dict") else None
        if payload is None and doc is not None:
            from dataclasses import asdict

            payload = asdict(doc)
            payload["as_of"] = doc.as_of.isoformat()
        return json.dumps(
            {"index_research": payload, "markdown": markdown},
            indent=2,
            default=str,
        )
    except Exception as e:
        return f"Error loading index trade plan: {str(e)}"


@mcp.tool()
def get_index_trade_widget(
    ticker: str = "NIFTY",
    refresh: bool = False,
    horizon_days: int | None = None,
) -> str:
    """
    Build a structured index trade-plan widget for Vibe chat.

    Includes prediction range, SHAP/marginal factor contributions, sensitivity
    curves (index vs factor shocks), and event-impact paths.

    Args:
        ticker: Index symbol (NIFTY, BANKNIFTY, …)
        refresh: Regenerate hub research before building widget
        horizon_days: Prediction horizon in days (default 14)

    Returns:
        JSON widget payload (type trade_plan.widget, asset_type index).
    """
    try:
        from trade_integrations.dataflows.index_research.widget_payload import (
            build_index_trade_widget,
        )

        widget = build_index_trade_widget(
            ticker,
            horizon_days=horizon_days,
            refresh=refresh,
            widget_intent="index_outlook",
        )
        widget_id = widget.get("widget_id")
        if widget_id:
            store = _trade_widget_store_dir() / f"{widget_id}.json"
            store.write_text(json.dumps(widget, indent=2, default=str), encoding="utf-8")
        return json.dumps(widget, indent=2, default=str)
    except Exception as e:
        return f"Error building index trade widget: {str(e)}"


@mcp.tool()
def get_pipeline_snapshot(ticker: str = "NIFTY", pipeline_as_of: str = "") -> str:
    """Summarize the bound Analysis pipeline snapshot (spot, prediction, contributors)."""
    try:
        from trade_integrations.dataflows.index_research.news_scenario_tools import (
            tool_get_pipeline_snapshot,
        )

        return tool_get_pipeline_snapshot(ticker, pipeline_as_of)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def query_factor_explanation(ticker: str = "NIFTY", pipeline_as_of: str = "", limit: int = 8) -> str:
    """Top macro factor contributors from the bound pipeline snapshot."""
    try:
        from trade_integrations.dataflows.index_research.news_scenario_tools import (
            tool_query_factor_explanation,
        )

        return tool_query_factor_explanation(ticker, pipeline_as_of, limit=limit)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def query_factor_sensitivity(ticker: str = "NIFTY", pipeline_as_of: str = "", limit: int = 8) -> str:
    """Factor sensitivity curves from the bound pipeline snapshot."""
    try:
        from trade_integrations.dataflows.index_research.news_scenario_tools import (
            tool_query_factor_sensitivity,
        )

        return tool_query_factor_sensitivity(ticker, pipeline_as_of, limit=limit)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def query_equation_coefficients(ticker: str = "NIFTY", pipeline_as_of: str = "") -> str:
    """Ridge equation coefficients from the bound pipeline snapshot."""
    try:
        from trade_integrations.dataflows.index_research.news_scenario_tools import (
            tool_query_equation_coefficients,
        )

        return tool_query_equation_coefficients(ticker, pipeline_as_of)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def query_constituent_drivers(ticker: str = "NIFTY", pipeline_as_of: str = "", limit: int = 10) -> str:
    """Constituent drivers from the bound pipeline snapshot."""
    try:
        from trade_integrations.dataflows.index_research.news_scenario_tools import (
            tool_query_constituent_drivers,
        )

        return tool_query_constituent_drivers(ticker, pipeline_as_of, limit=limit)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_pipeline_news_items(
    ticker: str = "NIFTY",
    pipeline_as_of: str = "",
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
) -> str:
    """Verified headlines embedded in the pipeline snapshot (optional date filter)."""
    try:
        from trade_integrations.dataflows.index_research.news_scenario_tools import (
            tool_get_pipeline_news_items,
        )

        return tool_get_pipeline_news_items(
            ticker, pipeline_as_of, start_date=start_date, end_date=end_date, limit=limit
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_playground_context(ticker: str = "NIFTY", pipeline_as_of: str = "") -> str:
    """Playground factor/headline bundle from the bound pipeline snapshot."""
    try:
        from trade_integrations.dataflows.index_research.news_scenario_tools import (
            tool_get_playground_context,
        )

        return tool_get_playground_context(ticker, pipeline_as_of)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def simulate_pipeline_scenario(
    ticker: str = "NIFTY",
    pipeline_as_of: str = "",
    primary_factor: str | None = None,
    primary_shock_pct: float | None = None,
    horizon_days: int | None = None,
    factor_overrides_json: str = "{}",
) -> str:
    """Single-factor what-if on the bound pipeline snapshot."""
    try:
        from trade_integrations.dataflows.index_research.news_scenario_tools import (
            tool_simulate_pipeline_scenario,
        )

        factor_overrides = None
        if factor_overrides_json and factor_overrides_json.strip() not in ("", "{}"):
            factor_overrides = json.loads(factor_overrides_json)
        return tool_simulate_pipeline_scenario(
            ticker,
            pipeline_as_of,
            factor_overrides=factor_overrides,
            primary_factor=primary_factor,
            primary_shock_pct=primary_shock_pct,
            horizon_days=horizon_days,
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def save_news_scenario_draft(
    ticker: str = "NIFTY",
    pipeline_as_of: str = "",
    draft_json: str = "{}",
) -> str:
    """Save a news scenario draft (event + outcomes) before quant run."""
    try:
        from trade_integrations.dataflows.index_research.news_scenario_tools import (
            tool_save_news_scenario_draft,
        )

        return tool_save_news_scenario_draft(ticker, pipeline_as_of, draft_json)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def run_news_event_scenario(
    ticker: str = "NIFTY",
    pipeline_as_of: str = "",
    draft_id: str = "",
    session_id: str | None = None,
) -> str:
    """Run quant paths for all outcomes in a saved draft."""
    try:
        from trade_integrations.dataflows.index_research.news_scenario_tools import (
            tool_run_news_event_scenario,
        )

        return tool_run_news_event_scenario(
            ticker, pipeline_as_of, draft_id, session_id=session_id
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_news_scenario_widget(
    ticker: str = "NIFTY",
    pipeline_as_of: str = "",
    scenario_id: str = "",
    selected_outcome_id: str | None = None,
) -> str:
    """Build a news_event_scenario trade_plan.widget from a saved scenario."""
    try:
        from trade_integrations.dataflows.index_research.news_scenario_tools import (
            tool_get_news_scenario_widget,
        )

        return tool_get_news_scenario_widget(
            ticker, pipeline_as_of, scenario_id, selected_outcome_id=selected_outcome_id
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_market_depth(symbol: str, exchange: str = "NSE") -> str:
    """
    Get market depth (order book) for a symbol.

    Args:
        symbol: Stock symbol
        exchange: Exchange name
    """
    try:
        response = client.depth(symbol=symbol.upper(), exchange=exchange.upper())
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting market depth: {str(e)}"


@mcp.tool()
def get_historical_data(
    symbol: str,
    exchange: str,
    interval: str,
    start_date: str | None = None,
    end_date: str | None = None,
    source: str = "api",
    bars: int = 20,
    lookback_days: int | None = None,
) -> str:
    """
    Get historical OHLCV data for a symbol.

    Args:
        symbol: Stock symbol
        exchange: Exchange name
        interval: Time interval. With source='api': '1m', '3m', '5m', '10m', '15m', '30m', '1h', 'D'.
                  With source='db': also supports custom intervals (2m, 4m, 6m, 7m, 2h, 3h, 4h) and
                  daily-based (W, M, Q, Y plus multiples like 2W, 3M).
        start_date: Start date (YYYY-MM-DD). Optional — when omitted, the last `bars`
                    (default 20) most-recent bars are returned (or `lookback_days` if given).
        end_date: End date (YYYY-MM-DD). Optional — defaults to today.
        source: 'api' (default) fetches from broker API. 'db' fetches from the local
                OpenAlgo Historify DuckDB store (1m/D stored, other intervals computed via SQL).
        bars: Number of most-recent bars to return (default 20). The window is fetched
              server-side; only the last `bars` rows are sent back to keep the payload small.
              Increase only if you explicitly need more rows.
        lookback_days: When dates are omitted, fetch the last N calendar days instead of a
                       bar-count window (e.g., 30 for "last 30 days").

    Returns:
        JSON with total count, returned count, a truncated flag, and data (list of
        {timestamp, open, high, low, close, volume}) — the last `bars` rows.
    """
    try:
        # Fetch enough to satisfy `bars` (min 252) unless an explicit range/lookback is given.
        response = _load_history(
            symbol, exchange, interval, start_date, end_date, max(252, bars), lookback_days, source
        )
        total = len(response)
        return json.dumps(
            {
                "count": total,
                "returned": min(bars, total),
                "truncated": total > bars,
                "bars": bars,
                "data": _df_records(response, bars),
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        return f"Error getting historical data: {str(e)}"


# INSTRUMENT SEARCH AND INFO TOOLS


@mcp.tool()
def search_instruments(
    query: str, exchange: str | None = None, instrument_type: str | None = None
) -> str:
    """
    Search for instruments by name or symbol.

    Args:
        query: Search query (e.g., 'NIFTY 26000 DEC CE', 'RELIANCE')
        exchange: Exchange to restrict the search to (NSE, BSE, NFO, BFO, MCX, NSE_INDEX, etc.).
                  Optional — when omitted, searches across all exchanges.
        instrument_type: Optional convenience filter — pass 'INDEX' to auto-rewrite
                         exchange=NSE → NSE_INDEX and BSE → BSE_INDEX.
    """
    try:
        resolved_exchange = exchange
        if instrument_type and instrument_type.upper() == "INDEX" and exchange:
            if exchange.upper() == "NSE":
                resolved_exchange = "NSE_INDEX"
            elif exchange.upper() == "BSE":
                resolved_exchange = "BSE_INDEX"
        if resolved_exchange is not None:
            response = client.search(query=query, exchange=resolved_exchange.upper())
        else:
            response = client.search(query=query)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error searching instruments: {str(e)}"


@mcp.tool()
def get_symbol_info(symbol: str, exchange: str = "NSE", instrument_type: str = None) -> str:
    """
    Get detailed information about a symbol.

    Args:
        symbol: Stock symbol
        exchange: Exchange name
        instrument_type: Optional - 'INDEX' for index symbols
    """
    try:
        # Handle index symbols
        if instrument_type and instrument_type.upper() == "INDEX":
            if exchange.upper() == "NSE":
                exchange = "NSE_INDEX"
            elif exchange.upper() == "BSE":
                exchange = "BSE_INDEX"

        # Or auto-route to the _INDEX exchange if the symbol is a known index.
        if symbol.upper() in NSE_INDEX_SYMBOLS and exchange.upper() == "NSE":
            exchange = "NSE_INDEX"
        elif symbol.upper() in BSE_INDEX_SYMBOLS and exchange.upper() == "BSE":
            exchange = "BSE_INDEX"

        response = client.symbol(symbol=symbol.upper(), exchange=exchange.upper())
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting symbol info: {str(e)}"


@mcp.tool()
def get_index_symbols(exchange: str = "NSE") -> str:
    """
    Get the OpenAlgo-standardized index symbols for NSE or BSE.

    These are the common index names rolled out across all supported brokers via the
    OpenAlgo symbol standardization. Use exchange code 'NSE_INDEX' / 'BSE_INDEX' when
    placing orders or fetching quotes for these symbols.

    Args:
        exchange: NSE or BSE

    Returns:
        JSON with exchange, exchange_code, and the full list of standardized index
        symbols (57+ NSE, 40+ BSE).
    """
    indices = {
        "NSE": {"exchange_code": "NSE_INDEX", "symbols": NSE_INDEX_SYMBOLS},
        "BSE": {"exchange_code": "BSE_INDEX", "symbols": BSE_INDEX_SYMBOLS},
    }

    exchange_upper = exchange.upper()
    if exchange_upper in indices:
        return json.dumps(
            {
                "exchange": exchange_upper,
                "exchange_code": indices[exchange_upper]["exchange_code"],
                "indices": indices[exchange_upper]["symbols"],
            },
            indent=2,
        )
    else:
        return json.dumps({"error": f"Unknown exchange: {exchange}. Use NSE or BSE."}, indent=2)


@mcp.tool()
def get_expiry_dates(symbol: str, exchange: str = "NFO", instrument_type: str = "options") -> str:
    """
    Get expiry dates for derivatives.

    Args:
        symbol: Underlying symbol
        exchange: Exchange name (typically NFO for F&O)
        instrument_type: 'options' or 'futures'
    """
    try:
        response = client.expiry(
            symbol=symbol.upper(), exchange=exchange.upper(), instrumenttype=instrument_type.lower()
        )
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting expiry dates: {str(e)}"


@mcp.tool()
def get_available_intervals() -> str:
    """Get all available time intervals for historical data."""
    try:
        response = client.intervals()
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting intervals: {str(e)}"


@mcp.tool()
def get_option_symbol(
    underlying: str,
    exchange: str,
    offset: str,
    option_type: str,
    expiry_date: str | None = None,
) -> str:
    """
    Get option symbol for specific strike and expiry.

    Args:
        underlying: Underlying symbol (e.g., 'NIFTY', 'BANKNIFTY', 'NIFTY28OCT25FUT')
        exchange: Exchange for underlying ('NSE_INDEX', 'BSE_INDEX', 'NFO', 'BFO')
        offset: Strike offset - 'ATM', 'ITM1'-'ITM50', 'OTM1'-'OTM50'
        option_type: 'CE' for Call or 'PE' for Put
        expiry_date: Expiry date in 'DDMMMYY' format (e.g., '28OCT25'). Optional when
                     the underlying already includes an expiry.

    Returns:
        JSON with symbol, exchange, lotsize, tick_size, underlying_ltp
    """
    try:
        params: dict[str, Any] = {
            "underlying": underlying.upper(),
            "exchange": exchange.upper(),
            "offset": offset.upper(),
            "option_type": option_type.upper(),
        }
        if expiry_date is not None:
            params["expiry_date"] = expiry_date
        response = client.optionsymbol(**params)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error getting option symbol: {str(e)}"


@mcp.tool()
def get_synthetic_future(underlying: str, exchange: str, expiry_date: str) -> str:
    """
    Calculate synthetic future price using put-call parity.

    Args:
        underlying: Underlying symbol (e.g., 'NIFTY', 'BANKNIFTY')
        exchange: Exchange for underlying ('NSE_INDEX', 'BSE_INDEX')
        expiry_date: Expiry date in format 'DDMMMYY' (e.g., '25NOV25')

    Returns:
        JSON with atm_strike, expiry, status, synthetic_future_price, underlying, underlying_ltp
    """
    try:
        response = client.syntheticfuture(
            underlying=underlying.upper(), exchange=exchange.upper(), expiry_date=expiry_date
        )
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error calculating synthetic future: {str(e)}"


@mcp.tool()
def get_option_greeks(
    symbol: str,
    exchange: str,
    interest_rate: float | None = None,
    forward_price: float | None = None,
    underlying_symbol: str | None = None,
    underlying_exchange: str | None = None,
    expiry_time: str | None = None,
) -> str:
    """
    Calculate option Greeks (Delta, Gamma, Theta, Vega, Rho) and Implied Volatility using Black-76.

    Args:
        symbol: Option symbol (e.g., 'NIFTY25NOV2526000CE'). Required.
        exchange: Exchange code ('NFO', 'BFO', 'CDS', 'MCX'). Required.
        interest_rate: Risk-free interest rate in annualized % (e.g., 6.5 for RBI repo).
                       Optional — defaults to 0.
        forward_price: Custom forward / synthetic futures price. If provided, skips the
                       underlying price fetch. Useful for illiquid underlyings (FINNIFTY,
                       MIDCPNIFTY) or custom scenario analysis.
        underlying_symbol: Custom underlying symbol (e.g., 'NIFTY', 'NIFTY30DEC25FUT').
                           Optional — auto-detected from the option symbol when omitted.
        underlying_exchange: Custom underlying exchange ('NSE_INDEX', 'NFO', etc.).
                             Optional — auto-detected when omitted.
        expiry_time: Custom expiry time in HH:MM format (e.g., '19:00'). Required for
                     MCX contracts with non-standard expiry times. Exchange defaults:
                     NFO/BFO=15:30, CDS=12:30, MCX=23:30.

    Returns:
        JSON with greeks, implied_volatility, spot_price, strike, days_to_expiry.
    """
    try:
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
        }
        if interest_rate is not None:
            params["interest_rate"] = interest_rate
        if forward_price is not None:
            params["forward_price"] = forward_price
        if underlying_symbol is not None:
            params["underlying_symbol"] = underlying_symbol.upper()
        if underlying_exchange is not None:
            params["underlying_exchange"] = underlying_exchange.upper()
        if expiry_time is not None:
            params["expiry_time"] = expiry_time
        response = client.optiongreeks(**params)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error calculating option greeks: {str(e)}"


# UTILITY TOOLS


@mcp.tool()
def get_openalgo_version() -> str:
    """Get the OpenAlgo library version."""
    try:
        import openalgo

        return f"OpenAlgo version: {openalgo.__version__}"
    except Exception as e:
        return f"Error getting version: {str(e)}"


@mcp.tool()
def validate_order_constants() -> str:
    """Display all valid order constants for reference."""
    constants = {
        "exchanges": {
            "NSE": "NSE Equity",
            "NFO": "NSE Futures & Options",
            "CDS": "NSE Currency",
            "BSE": "BSE Equity",
            "BFO": "BSE Futures & Options",
            "BCD": "BSE Currency",
            "MCX": "MCX Commodity",
            "NCDEX": "NCDEX Commodity",
        },
        "product_types": {
            "CNC": "Cash & Carry for equity",
            "NRML": "Normal for futures and options",
            "MIS": "Intraday Square off",
        },
        "price_types": {
            "MARKET": "Market Order",
            "LIMIT": "Limit Order",
            "SL": "Stop Loss Limit Order",
            "SL-M": "Stop Loss Market Order",
        },
        "actions": {"BUY": "Buy", "SELL": "Sell"},
        "intervals": ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "D"],
    }
    return json.dumps(constants, indent=2)


@mcp.tool()
def send_telegram_alert(username: str, message: str, priority: int = 5) -> str:
    """
    Send a Telegram alert notification.

    Args:
        username: OpenAlgo login ID/username
        message: Alert message to send
        priority: Notification priority (1-10, default 5). Higher values may be used
                  by the bot for emphasis/sorting depending on configuration.

    Returns:
        JSON with status and message
    """
    try:
        response = client.telegram(username=username, message=message, priority=priority)
        return json.dumps(response, indent=2)
    except Exception as e:
        return f"Error sending telegram alert: {str(e)}"


@mcp.tool()
def get_holidays(year: int | None = None) -> str:
    """
    Get trading holidays for a specific year.

    Args:
        year: Year to get holidays for (e.g., 2026). Optional — defaults to current year.

    Returns:
        JSON with list of trading holidays including:
        - date: Holiday date (YYYY-MM-DD)
        - description: Holiday name/reason
        - holiday_type: TRADING_HOLIDAY, SETTLEMENT_HOLIDAY, or SPECIAL_SESSION
        - closed_exchanges: List of closed exchanges
        - open_exchanges: List of exchanges with special timings

    Example:
        get_holidays(2026)
        get_holidays()          # current year
    """
    try:
        response = client.holidays(year=year) if year is not None else client.holidays()
        return json.dumps(response, indent=2, default=str)
    except Exception as e:
        return f"Error getting holidays: {str(e)}"


@mcp.tool()
def get_timings(date: str | None = None) -> str:
    """
    Get exchange trading timings for a specific date.

    Args:
        date: Date in YYYY-MM-DD format (e.g., '2026-04-23'). Optional — defaults to today.

    Returns:
        JSON with exchange timings including:
        - exchange: Exchange name (NSE, BSE, NFO, BFO, MCX, CDS, BCD)
        - start_time: Market open time in epoch milliseconds
        - end_time: Market close time in epoch milliseconds

    Example:
        get_timings("2026-04-23")
        get_timings()           # today
    """
    try:
        response = client.timings(date=date) if date is not None else client.timings()
        return json.dumps(response, indent=2, default=str)
    except Exception as e:
        return f"Error getting timings: {str(e)}"


@mcp.tool()
def check_holiday(date: str, exchange: str | None = None) -> str:
    """
    Check if a specific date is a market holiday for an exchange.

    This calls the /api/v1/checkholiday endpoint directly (not yet in the openalgo SDK).
    Use this for fast pre-trade "is the market open?" checks.

    Args:
        date: Date in YYYY-MM-DD format (between 2020-01-01 and 2050-12-31). Required.
        exchange: Exchange code (NSE, BSE, NFO, BFO, MCX, CDS, BCD). Optional.
                  When omitted, returns true if the date is a holiday for any major exchange.

    Returns:
        JSON with:
        - status: 'success' or 'error'
        - data.date, data.exchange (if specified), data.is_holiday (bool)

    Notes:
        - Weekends and national holidays both return is_holiday=true.
        - For a full calendar, use get_holidays(year).

    Examples:
        check_holiday("2026-01-26", "NSE")
        check_holiday("2026-01-27")
    """
    try:
        url = f"{host.rstrip('/')}/api/v1/checkholiday"
        payload: dict[str, Any] = {"apikey": api_key, "date": date}
        if exchange:
            payload["exchange"] = exchange.upper()
        with httpx.Client(timeout=30.0) as http:
            r = http.post(url, json=payload, headers={"Content-Type": "application/json"})
            return json.dumps(r.json(), indent=2, default=str)
    except Exception as e:
        return f"Error checking holiday: {str(e)}"


@mcp.tool()
def get_instruments(exchange: str | None = None, limit: int = 500) -> str:
    """
    Download the full instrument master.

    Args:
        exchange: Exchange name (NSE, BSE, NFO, BFO, MCX, CDS, BCD, NSE_INDEX, BSE_INDEX).
                  Optional — when omitted, downloads instruments for ALL exchanges.
        limit: Maximum number of rows to return in the response (default: 500).
               The full dataset can exceed 100k rows for derivatives exchanges, which
               overwhelms the MCP tool output. Use search_instruments for targeted lookups.

    Returns:
        JSON with count, returned, truncated flag, and data (list of instrument records).
        Each record includes: symbol, brsymbol, name, exchange, lotsize,
        instrumenttype, expiry, strike, token, tick_size.
    """
    try:
        response = (
            client.instruments(exchange=exchange.upper())
            if exchange is not None
            else client.instruments()
        )
        # SDK returns a DataFrame on success, dict on error
        if hasattr(response, "reset_index"):
            total = len(response)
            df_head = response.head(limit).reset_index(drop=True)
            return json.dumps(
                {
                    "exchange": exchange.upper() if exchange else "ALL",
                    "count": total,
                    "returned": len(df_head),
                    "truncated": total > limit,
                    "limit": limit,
                    "data": df_head.to_dict(orient="records"),
                },
                indent=2,
                default=str,
            )
        return json.dumps(response, indent=2, default=str)
    except Exception as e:
        return f"Error getting instruments: {str(e)}"


# Tool to get analyzer status
@mcp.tool()
def analyzer_status() -> str:
    """
    Get the current analyzer status including mode and total logs.

    Returns:
        JSON with analyzer status information:
        - data.analyze_mode: Boolean indicating if analyzer is active
        - data.mode: Current mode ('analyze' or 'live')
        - data.total_logs: Number of logs in analyzer
        - status: 'success' or 'error'
    """
    try:
        response = client.analyzerstatus()
        return json.dumps(response, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


# Tool to toggle analyzer mode
@mcp.tool()
def analyzer_toggle(mode: bool) -> str:
    """
    Toggle the analyzer mode between analyze (simulated) and live trading.

    Args:
        mode: True for analyze mode (simulated), False for live mode

    Returns:
        JSON with updated analyzer status:
        - data.analyze_mode, data.message, data.mode, data.total_logs
        - status: 'success' or 'error'

    Example:
        analyzer_toggle(True)  # Switch to analyze mode (simulated responses)
        analyzer_toggle(False) # Switch to live trading mode
    """
    try:
        response = client.analyzertoggle(mode=mode)
        return json.dumps(response, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2)


# ============================================================
# RESEARCH TOOLS — TECHNICAL INDICATORS (openalgo.ta)
# ============================================================
# These tools fetch OHLCV history via the SDK (client.history) and
# compute indicators with `from openalgo import ta`. They are SDK-only
# and work under BOTH the stdio and HTTP transports.

# Indicators whose first inputs are High/Low/Close (and optionally
# Volume) rather than a single Close series. Used to auto-pick inputs
# in calculate_indicator() when the caller does not pass `inputs`.
_HLC_INDICATORS = {
    "atr", "natr", "true_range", "adx", "adxr", "dmi", "dx", "supertrend",
    "stochastic", "stochf", "cci", "williams_r", "keltner", "donchian",
    "aroon", "aroon_oscillator", "psar", "ichimoku", "pivot_points",
    "ultimate_oscillator", "uo_oscillator", "chandelier_exit", "starc",
    "elderray", "ckstop", "fractals", "rwi", "alligator", "gator_oscillator",
    "bop", "rvi", "fisher", "avgprice", "medprice", "midprice", "typprice",
    "wclprice",
}
_HLCV_INDICATORS = {"mfi", "cmf", "adl", "emv", "klingervolumeoscillator"}


def _history_df(
    symbol: str,
    exchange: str,
    interval: str,
    start_date: str,
    end_date: str,
    source: str = "api",
):
    """Fetch OHLCV history as a timestamp-indexed DataFrame. Raises on failure.

    source: 'api' (default) fetches from the broker API; 'db' fetches from the local
    OpenAlgo Historify DuckDB store (1m/D stored, other intervals computed via SQL,
    enabling custom intervals like 2m/4m/W/M/Q for research).
    """
    df = client.history(
        symbol=symbol.upper(),
        exchange=exchange.upper(),
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        source=source,
    )
    # SDK returns a DataFrame on success, a dict on error.
    if not hasattr(df, "reset_index"):
        raise ValueError(f"history error: {df}")
    if len(df) == 0:
        raise ValueError("no historical data returned for the given range")
    return df


# Approx bars per trading day per interval (NSE ~6h15m session). Used only to size
# the calendar window when fetching by bar-count, so it can be a rough estimate.
_BARS_PER_DAY = {
    "1m": 375, "3m": 125, "5m": 75, "10m": 38, "15m": 25, "30m": 13,
    "1h": 7, "60m": 7, "2h": 4, "3h": 3, "4h": 2,
    "d": 1, "day": 1, "w": 0.2, "week": 0.2, "m": 0.05, "month": 0.05,
}


def _load_history(
    symbol: str,
    exchange: str,
    interval: str,
    start_date: str | None = None,
    end_date: str | None = None,
    lookback_bars: int = 252,
    lookback_days: int | None = None,
    source: str = "api",
):
    """Fetch OHLCV history with a flexible lookback window.

    Resolution priority:
      1. Explicit start_date (with optional end_date) -> use that range verbatim.
      2. lookback_days given -> last N calendar days ending today (e.g., "last 30 days").
      3. else -> last `lookback_bars` bars (default 252 ≈ one trading year of daily data):
         fetch a wide-enough calendar window, then tail to exactly `lookback_bars` rows.
    """
    end = end_date or date.today().isoformat()
    if start_date:
        return _history_df(symbol, exchange, interval, start_date, end, source)
    if lookback_days:
        start = (date.fromisoformat(end) - timedelta(days=int(lookback_days))).isoformat()
        return _history_df(symbol, exchange, interval, start, end, source)
    bpd_per_day = _BARS_PER_DAY.get(interval.lower(), 75)
    cal_days = int((lookback_bars / bpd_per_day) * 1.6) + 5
    start = (date.fromisoformat(end) - timedelta(days=cal_days)).isoformat()
    df = _history_df(symbol, exchange, interval, start, end, source)
    return df.tail(int(lookback_bars))


def _idx_iso(df, pos: int) -> str:
    """ISO timestamp of the row at positional index `pos` (e.g., 0 first, -1 last)."""
    ts = df.index[pos]
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


def _last(series) -> float | None:
    """Last non-null value of a Series-like as a rounded float, or None."""
    try:
        s = series.dropna()
        return round(float(s.iloc[-1]), 4) if len(s) else None
    except Exception:
        return None


def _df_records(df, limit: int | None = None):
    """Convert a DataFrame to JSON-safe records with an ISO 'timestamp' column."""
    out = df.tail(limit) if limit else df
    out = out.reset_index()
    out = out.rename(columns={out.columns[0]: "timestamp"})
    return json.loads(out.to_json(orient="records", date_format="iso"))


def _resolve_inputs(df, name: str, inputs: list[str] | None):
    """Pick the ordered input Series for an indicator (caller override or heuristic)."""
    if inputs:
        cols = [c.lower() for c in inputs]
    elif name in _HLCV_INDICATORS:
        cols = ["high", "low", "close", "volume"]
    elif name in _HLC_INDICATORS:
        cols = ["high", "low", "close"]
    else:
        cols = ["close"]
    return cols, [df[c] for c in cols]


def _bundle(df, specs: list[tuple]):
    """Compute latest values for a set of indicators, capturing per-item errors.

    specs: list of (key, callable) where callable returns a Series or tuple of Series.
    Tuple results become a list of latest values (see each tool's 'legend').
    """
    result: dict[str, Any] = {}
    for key, fn in specs:
        try:
            val = fn()
            result[key] = [_last(s) for s in val] if isinstance(val, tuple) else _last(val)
        except Exception as e:
            result[key] = {"error": str(e)}
    return result


def _as_bool(x, index) -> pd.Series:
    """Coerce an indicator boolean result (Series or array) to a clean bool Series."""
    s = pd.Series(list(x), index=index)
    return s.fillna(False).astype(bool)


@mcp.tool()
def calculate_indicator(
    symbol: str,
    exchange: str,
    indicator: str,
    interval: str = "D",
    start_date: str | None = None,
    end_date: str | None = None,
    params: dict[str, Any] | None = None,
    inputs: list[str] | None = None,
    bars: int = 20,
    lookback_bars: int = 252,
    lookback_days: int | None = None,
    source: str = "api",
) -> str:
    """
    Run ANY of the 80+ openalgo.ta indicators over a symbol's historical OHLCV.

    History is fetched (db/api) and the indicator is computed entirely on the
    OpenAlgo server; only compact results are returned — never the raw OHLCV.

    Args:
        symbol: Stock symbol (e.g., 'RELIANCE', 'NIFTY')
        exchange: Exchange name (NSE, NFO, NSE_INDEX, etc.)
        indicator: ta function name, case-insensitive (e.g., 'rsi','macd','supertrend',
                   'atr','bbands','adx','ema','vwap').
        interval: '1m','3m','5m','10m','15m','30m','1h','D' (default 'D')
        start_date / end_date: YYYY-MM-DD. Optional — when omitted, a lookback window
                   ending today is used.
        params: Extra keyword args for the indicator (e.g., {"period": 14} for rsi;
                {"period": 10, "multiplier": 3} for supertrend;
                {"fast_period": 12, "slow_period": 26, "signal_period": 9} for macd).
        inputs: Ordered list of OHLCV columns to feed the indicator, e.g. ["close"] or
                ["high","low","close"]. Optional — auto-detected for common indicators;
                pass it explicitly if a result errors on inputs.
        bars: Number of most-recent computed rows to return (default 20). The indicator
              is ALWAYS computed server-side over the FULL fetched history; only the last
              `bars` rows (plus latest value and summary stats) are sent back, so the
              payload stays small. Increase only if you explicitly need more rows.
        lookback_bars: Bars of history to load/compute over when dates are omitted
                       (default 252 ≈ one trading year of daily data).
        lookback_days: Alternative calendar-day lookback (e.g., 30 for "last 30 days").
                       Overrides lookback_bars when set.
        source: 'api' (default, broker API) or 'db' (local Historify DuckDB store, which
                supports custom research intervals like 2m/4m/W/M/Q).

    Returns:
        JSON with the latest value(s), summary stats (last/min/max/mean), and a 'data'
        series of the last `bars` rows — all computed server-side. Multi-output indicators
        (macd, bbands, supertrend, stochastic, adx, ichimoku, keltner, donchian) report
        out0, out1, ...
    """
    try:
        df = _load_history(
            symbol, exchange, interval, start_date, end_date, lookback_bars, lookback_days, source
        )
        name = indicator.lower()
        fn = getattr(ta, name, None)
        if fn is None:
            return json.dumps(
                {"status": "error", "message": f"unknown indicator '{indicator}'"}, indent=2
            )
        cols, args = _resolve_inputs(df, name, inputs)
        result = fn(*args, **(params or {}))
        out = pd.DataFrame(index=df.index)
        if isinstance(result, tuple):
            for i, s in enumerate(result):
                out[f"out{i}"] = pd.Series(list(s), index=df.index)
        else:
            out["value"] = pd.Series(list(result), index=df.index)

        def _stats(s):
            s = s.dropna()
            if not len(s):
                return None
            return {
                "last": round(float(s.iloc[-1]), 4),
                "min": round(float(s.min()), 4),
                "max": round(float(s.max()), 4),
                "mean": round(float(s.mean()), 4),
            }

        cols_out = list(out.columns)
        ts = out.index[-1]
        payload: dict[str, Any] = {
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
            "indicator": name,
            "inputs": cols,
            "params": params or {},
            "interval": interval,
            "source": source,
            "bars": len(out),
            "last_close": _last(df["close"]),
            "latest_timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "latest": {c: _last(out[c]) for c in cols_out},
            "summary": {c: _stats(out[c]) for c in cols_out},
            "returned_bars": min(bars, len(out)),
            "data": _df_records(out, bars),
        }
        return json.dumps(payload, indent=2, default=str)
    except Exception as e:
        return f"Error calculating indicator: {str(e)}"


@mcp.tool()
def get_trend_snapshot(
    symbol: str,
    exchange: str,
    interval: str = "D",
    start_date: str | None = None,
    end_date: str | None = None,
    lookback_bars: int = 252,
    lookback_days: int | None = None,
    source: str = "api",
) -> str:
    """
    One-call trend read: SMA(20/50/200), EMA(20/50), Supertrend, ADX/DMI, Ichimoku.

    Args:
        symbol: Stock symbol
        exchange: Exchange name
        interval: Candle interval (default 'D')
        start_date / end_date: YYYY-MM-DD. Optional — default to a lookback window ending today.
        lookback_bars: Bars of history loaded when dates are omitted (default 252, enough for SMA200).
        lookback_days: Alternative calendar-day lookback (e.g., 30). Overrides lookback_bars.

    Returns:
        JSON with latest indicator values and a 'legend' explaining multi-value entries.
    """
    try:
        df = _load_history(
            symbol, exchange, interval, start_date, end_date, lookback_bars, lookback_days, source
        )
        snap = _bundle(
            df,
            [
                ("sma_20", lambda: ta.sma(df["close"], 20)),
                ("sma_50", lambda: ta.sma(df["close"], 50)),
                ("sma_200", lambda: ta.sma(df["close"], 200)),
                ("ema_20", lambda: ta.ema(df["close"], 20)),
                ("ema_50", lambda: ta.ema(df["close"], 50)),
                ("supertrend", lambda: ta.supertrend(df["high"], df["low"], df["close"])),
                ("adx_di", lambda: ta.adx(df["high"], df["low"], df["close"], period=14)),
                ("ichimoku", lambda: ta.ichimoku(df["high"], df["low"], df["close"])),
            ],
        )
        return json.dumps(
            {
                "symbol": symbol.upper(),
                "exchange": exchange.upper(),
                "interval": interval,
                "from": _idx_iso(df, 0),
                "to": _idx_iso(df, -1),
                "bars_loaded": len(df),
                "last_close": _last(df["close"]),
                "indicators": snap,
                "legend": {
                    "supertrend": "[supertrend_value, direction(+1 up / -1 down)]",
                    "adx_di": "[+DI, -DI, ADX]",
                    "ichimoku": "[tenkan, kijun, senkou_a, senkou_b, chikou]",
                },
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        return f"Error getting trend snapshot: {str(e)}"


@mcp.tool()
def get_momentum_snapshot(
    symbol: str,
    exchange: str,
    interval: str = "D",
    start_date: str | None = None,
    end_date: str | None = None,
    lookback_bars: int = 252,
    lookback_days: int | None = None,
    source: str = "api",
) -> str:
    """
    One-call momentum read: RSI(14), MACD, Stochastic, CCI(20), Williams %R(14).

    Args:
        symbol: Stock symbol
        exchange: Exchange name
        interval: Candle interval (default 'D')
        start_date / end_date: YYYY-MM-DD. Optional — default to a lookback window ending today.
        lookback_bars: Bars of history loaded when dates are omitted (default 252).
        lookback_days: Alternative calendar-day lookback (e.g., 30). Overrides lookback_bars.

    Returns:
        JSON with latest values and a 'legend' for multi-value entries.
    """
    try:
        df = _load_history(
            symbol, exchange, interval, start_date, end_date, lookback_bars, lookback_days, source
        )
        snap = _bundle(
            df,
            [
                ("rsi_14", lambda: ta.rsi(df["close"], 14)),
                ("macd", lambda: ta.macd(df["close"])),
                ("stochastic", lambda: ta.stochastic(df["high"], df["low"], df["close"])),
                ("cci_20", lambda: ta.cci(df["high"], df["low"], df["close"], 20)),
                ("williams_r_14", lambda: ta.williams_r(df["high"], df["low"], df["close"], 14)),
            ],
        )
        return json.dumps(
            {
                "symbol": symbol.upper(),
                "exchange": exchange.upper(),
                "interval": interval,
                "from": _idx_iso(df, 0),
                "to": _idx_iso(df, -1),
                "bars_loaded": len(df),
                "last_close": _last(df["close"]),
                "indicators": snap,
                "legend": {
                    "macd": "[macd_line, signal_line, histogram]",
                    "stochastic": "[%K, %D]",
                },
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        return f"Error getting momentum snapshot: {str(e)}"


@mcp.tool()
def get_volatility_snapshot(
    symbol: str,
    exchange: str,
    interval: str = "D",
    start_date: str | None = None,
    end_date: str | None = None,
    lookback_bars: int = 252,
    lookback_days: int | None = None,
    source: str = "api",
) -> str:
    """
    One-call volatility read: ATR, NATR, Bollinger Bands (+%B, width), Keltner,
    Donchian, Historical Volatility.

    Args:
        symbol: Stock symbol
        exchange: Exchange name
        interval: Candle interval (default 'D')
        start_date / end_date: YYYY-MM-DD. Optional — default to a lookback window ending today.
        lookback_bars: Bars of history loaded when dates are omitted (default 252).
        lookback_days: Alternative calendar-day lookback (e.g., 30). Overrides lookback_bars.

    Returns:
        JSON with latest values and a 'legend' for multi-value band entries.
    """
    try:
        df = _load_history(
            symbol, exchange, interval, start_date, end_date, lookback_bars, lookback_days, source
        )
        snap = _bundle(
            df,
            [
                ("atr_14", lambda: ta.atr(df["high"], df["low"], df["close"], period=14)),
                ("natr_14", lambda: ta.natr(df["high"], df["low"], df["close"], period=14)),
                ("bbands", lambda: ta.bbands(df["close"], period=20, std_dev=2.0)),
                ("bb_percent_b", lambda: ta.bbpercent(df["close"], period=20, std_dev=2.0)),
                ("bb_width", lambda: ta.bbwidth(df["close"], period=20, std_dev=2.0)),
                ("keltner", lambda: ta.keltner(df["high"], df["low"], df["close"])),
                ("donchian", lambda: ta.donchian(df["high"], df["low"], period=20)),
                ("historical_volatility", lambda: ta.hv(df["close"])),
            ],
        )
        return json.dumps(
            {
                "symbol": symbol.upper(),
                "exchange": exchange.upper(),
                "interval": interval,
                "from": _idx_iso(df, 0),
                "to": _idx_iso(df, -1),
                "bars_loaded": len(df),
                "last_close": _last(df["close"]),
                "indicators": snap,
                "legend": {
                    "bbands": "[upper, middle, lower]",
                    "keltner": "[upper, middle, lower]",
                    "donchian": "[upper, middle, lower]",
                },
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        return f"Error getting volatility snapshot: {str(e)}"


@mcp.tool()
def get_support_resistance(
    symbol: str,
    exchange: str,
    interval: str = "D",
    start_date: str | None = None,
    end_date: str | None = None,
    lookback_bars: int = 252,
    lookback_days: int | None = None,
    period: int = 20,
    source: str = "api",
) -> str:
    """
    Support/resistance levels: Pivot Points, Donchian channel, and rolling
    highest-high / lowest-low over `period`.

    Args:
        symbol: Stock symbol
        exchange: Exchange name
        interval: Candle interval (default 'D')
        start_date / end_date: YYYY-MM-DD. Optional — default to a lookback window ending today.
        lookback_bars: Bars of history loaded when dates are omitted (default 252).
        lookback_days: Alternative calendar-day lookback (e.g., 30). Overrides lookback_bars.
        period: Lookback window for Donchian / highest / lowest (default 20).

    Returns:
        JSON with latest levels. 'pivot_points' is returned as ta.pivot_points emits it
        (typically [pivot, r1, s1, r2, s2, r3, s3]).
    """
    try:
        df = _load_history(
            symbol, exchange, interval, start_date, end_date, lookback_bars, lookback_days, source
        )
        snap = _bundle(
            df,
            [
                ("donchian", lambda: ta.donchian(df["high"], df["low"], period=period)),
                ("highest_high", lambda: ta.highest(df["high"], period)),
                ("lowest_low", lambda: ta.lowest(df["low"], period)),
                ("pivot_points", lambda: ta.pivot_points(df["high"], df["low"], df["close"])),
            ],
        )
        return json.dumps(
            {
                "symbol": symbol.upper(),
                "exchange": exchange.upper(),
                "interval": interval,
                "from": _idx_iso(df, 0),
                "to": _idx_iso(df, -1),
                "bars_loaded": len(df),
                "period": period,
                "last_close": _last(df["close"]),
                "levels": snap,
                "legend": {"donchian": "[upper, middle, lower]"},
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        return f"Error getting support/resistance: {str(e)}"


@mcp.tool()
def detect_signals(
    symbol: str,
    exchange: str,
    interval: str = "D",
    start_date: str | None = None,
    end_date: str | None = None,
    signal_type: str = "ema_cross",
    fast: int = 20,
    slow: int = 50,
    period: int = 14,
    upper: float = 70.0,
    lower: float = 30.0,
    limit: int = 20,
    lookback_bars: int = 252,
    lookback_days: int | None = None,
    source: str = "api",
) -> str:
    """
    Detect technical signals over a symbol's history using ta crossover/threshold logic.

    Args:
        symbol: Stock symbol
        exchange: Exchange name
        interval: Candle interval (default 'D')
        start_date / end_date: YYYY-MM-DD. Optional — default to the lookback window.
        lookback_bars: Bars loaded when dates omitted (default 252).
        lookback_days: Alternative calendar-day lookback (e.g., 30). Overrides lookback_bars.
        signal_type: One of:
            'ema_cross'      - EMA(fast) crossing EMA(slow)
            'sma_cross'      - SMA(fast) crossing SMA(slow)
            'macd_cross'     - MACD line crossing its signal line
            'supertrend_flip'- Supertrend direction flip
            'rsi_threshold'  - RSI crossing out of oversold(lower) / overbought(upper)
        fast / slow: MA periods for ema_cross / sma_cross
        period: Lookback for rsi_threshold (default 14)
        upper / lower: RSI overbought / oversold levels (default 70 / 30)
        limit: Max number of most-recent signal events to return (default 20)

    Returns:
        JSON with recent events [{timestamp, signal: 'bullish'|'bearish'}] plus current values.
    """
    try:
        df = _load_history(
            symbol, exchange, interval, start_date, end_date, lookback_bars, lookback_days, source
        )
        close = df["close"]
        extra: dict[str, Any] = {}

        if signal_type in ("ema_cross", "sma_cross"):
            ma = ta.ema if signal_type == "ema_cross" else ta.sma
            f, s = ma(close, fast), ma(close, slow)
            bull = _as_bool(ta.crossover(f, s), df.index)
            bear = _as_bool(ta.crossunder(f, s), df.index)
            extra = {"fast": _last(f), "slow": _last(s)}
        elif signal_type == "macd_cross":
            line, sig, _hist = ta.macd(close)
            bull = _as_bool(ta.crossover(line, sig), df.index)
            bear = _as_bool(ta.crossunder(line, sig), df.index)
            extra = {"macd_line": _last(line), "signal_line": _last(sig)}
        elif signal_type == "supertrend_flip":
            st, d = ta.supertrend(df["high"], df["low"], close)
            d = pd.Series(list(d), index=df.index)
            bull = (d > 0) & (d.shift(1) <= 0)
            bear = (d < 0) & (d.shift(1) >= 0)
            bull, bear = bull.fillna(False), bear.fillna(False)
            extra = {"supertrend": _last(st), "direction": _last(d)}
        elif signal_type == "rsi_threshold":
            r = pd.Series(list(ta.rsi(close, period)), index=df.index)
            bull = (r > lower) & (r.shift(1) <= lower)
            bear = (r < upper) & (r.shift(1) >= upper)
            bull, bear = bull.fillna(False), bear.fillna(False)
            extra = {"rsi": _last(r)}
        else:
            return json.dumps(
                {"status": "error", "message": f"unknown signal_type '{signal_type}'"}, indent=2
            )

        def _stamp(ts):
            return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

        events = [{"timestamp": _stamp(ts), "signal": "bullish"} for ts, v in bull.items() if v]
        events += [{"timestamp": _stamp(ts), "signal": "bearish"} for ts, v in bear.items() if v]
        events.sort(key=lambda r: r["timestamp"])

        return json.dumps(
            {
                "symbol": symbol.upper(),
                "exchange": exchange.upper(),
                "interval": interval,
                "signal_type": signal_type,
                "last_close": _last(close),
                "current": extra,
                "event_count": len(events),
                "events": events[-limit:],
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        return f"Error detecting signals: {str(e)}"


@mcp.tool()
def screen_instruments(
    symbols: list[dict[str, str]],
    interval: str = "D",
    start_date: str | None = None,
    end_date: str | None = None,
    condition: str = "rsi_below",
    value: float = 30.0,
    period: int = 14,
    lookback_bars: int = 252,
    lookback_days: int | None = None,
    source: str = "api",
) -> str:
    """
    Scan a watchlist of symbols for a technical condition.

    Note: this fetches history per symbol sequentially — keep the list modest (≤ ~25)
    or use a coarse interval to bound runtime and broker API calls.

    Args:
        symbols: List of {"symbol","exchange"} pairs.
            Example: [{"symbol":"RELIANCE","exchange":"NSE"},{"symbol":"INFY","exchange":"NSE"}]
        interval: Candle interval (default 'D')
        start_date / end_date: YYYY-MM-DD. Optional — default to the lookback window.
        lookback_bars: Bars loaded per symbol when dates omitted (default 252).
        lookback_days: Alternative calendar-day lookback (e.g., 30). Overrides lookback_bars.
        condition: One of:
            'rsi_below' / 'rsi_above'        - RSI(period) vs `value`
            'price_above_sma'/'price_below_sma' - last close vs SMA(period)
            'supertrend_bullish'/'supertrend_bearish' - current Supertrend direction
        value: Threshold for rsi conditions (default 30)
        period: Lookback for rsi / sma (default 14)

    Returns:
        JSON with per-symbol {passed, metric} and a count of matches.
    """
    try:
        results = []
        for item in symbols:
            sym, exch = item.get("symbol", ""), item.get("exchange", "")
            try:
                df = _load_history(
                    sym, exch, interval, start_date, end_date, lookback_bars, lookback_days, source
                )
                close = df["close"]
                metric: Any = None
                passed = False
                if condition in ("rsi_below", "rsi_above"):
                    metric = _last(ta.rsi(close, period))
                    if metric is not None:
                        passed = metric < value if condition == "rsi_below" else metric > value
                elif condition in ("price_above_sma", "price_below_sma"):
                    sma, c = _last(ta.sma(close, period)), _last(close)
                    metric = c
                    if sma is not None and c is not None:
                        passed = c > sma if condition == "price_above_sma" else c < sma
                elif condition in ("supertrend_bullish", "supertrend_bearish"):
                    _st, d = ta.supertrend(df["high"], df["low"], close)
                    metric = _last(pd.Series(list(d), index=df.index))
                    if metric is not None:
                        passed = metric > 0 if condition == "supertrend_bullish" else metric < 0
                else:
                    return json.dumps(
                        {"status": "error", "message": f"unknown condition '{condition}'"}, indent=2
                    )
                results.append(
                    {"symbol": sym.upper(), "exchange": exch.upper(), "passed": passed, "metric": metric}
                )
            except Exception as e:
                results.append({"symbol": sym, "exchange": exch, "error": str(e)})

        matched = [r for r in results if r.get("passed")]
        return json.dumps(
            {
                "condition": condition,
                "value": value,
                "period": period,
                "scanned": len(symbols),
                "matched": len(matched),
                "results": results,
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        return f"Error screening instruments: {str(e)}"


@mcp.tool()
def multi_timeframe_analysis(
    symbol: str,
    exchange: str,
    start_date: str | None = None,
    end_date: str | None = None,
    intervals: list[str] | None = None,
    indicator: str = "rsi",
    params: dict[str, Any] | None = None,
    inputs: list[str] | None = None,
    lookback_bars: int = 252,
    lookback_days: int | None = None,
    source: str = "api",
) -> str:
    """
    Compute the same indicator across multiple timeframes for confluence analysis.

    Args:
        symbol: Stock symbol
        exchange: Exchange name
        start_date / end_date: YYYY-MM-DD. Optional — default to the lookback window per interval.
        intervals: List of intervals (default ['5m','15m','1h','D'])
        indicator: ta function name (default 'rsi')
        params: Extra keyword args for the indicator (e.g., {"period": 14})
        inputs: Ordered input columns; auto-detected if omitted.
        lookback_bars: Bars loaded per interval when dates omitted (default 252).
        lookback_days: Alternative calendar-day lookback (e.g., 30). Overrides lookback_bars.

    Returns:
        JSON with the latest indicator value (and last_close) per timeframe.
    """
    try:
        intervals = intervals or ["5m", "15m", "1h", "D"]
        name = indicator.lower()
        fn = getattr(ta, name, None)
        if fn is None:
            return json.dumps(
                {"status": "error", "message": f"unknown indicator '{indicator}'"}, indent=2
            )
        out: dict[str, Any] = {}
        for itv in intervals:
            try:
                df = _load_history(
                    symbol, exchange, itv, start_date, end_date, lookback_bars, lookback_days, source
                )
                _cols, args = _resolve_inputs(df, name, inputs)
                res = fn(*args, **(params or {}))
                value = [_last(s) for s in res] if isinstance(res, tuple) else _last(res)
                out[itv] = {"value": value, "last_close": _last(df["close"]), "bars": len(df)}
            except Exception as e:
                out[itv] = {"error": str(e)}
        return json.dumps(
            {
                "symbol": symbol.upper(),
                "exchange": exchange.upper(),
                "indicator": name,
                "params": params or {},
                "timeframes": out,
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        return f"Error in multi-timeframe analysis: {str(e)}"


@mcp.tool()
def correlation_beta(
    symbol1: str,
    exchange1: str,
    symbol2: str,
    exchange2: str,
    interval: str = "D",
    start_date: str | None = None,
    end_date: str | None = None,
    period: int = 20,
    lookback_bars: int = 252,
    lookback_days: int | None = None,
    source: str = "api",
) -> str:
    """
    Correlation / Beta / Linear-regression slope between two symbols (pairs & hedge research).

    Both symbols' closes are aligned on common timestamps before computing.

    Args:
        symbol1 / exchange1: First instrument (the 'asset')
        symbol2 / exchange2: Second instrument (the 'market'/benchmark)
        interval: Candle interval (default 'D')
        start_date / end_date: YYYY-MM-DD. Optional — default to the lookback window.
        period: Rolling window for correlation/beta/slope (default 20)
        lookback_bars: Bars loaded per symbol when dates omitted (default 252).
        lookback_days: Alternative calendar-day lookback (e.g., 30). Overrides lookback_bars.

    Returns:
        JSON with rolling correlation, rolling beta, LR slope of symbol1, the full-sample
        Pearson correlation, and the number of overlapping bars.
    """
    try:
        df1 = _load_history(
            symbol1, exchange1, interval, start_date, end_date, lookback_bars, lookback_days, source
        )
        df2 = _load_history(
            symbol2, exchange2, interval, start_date, end_date, lookback_bars, lookback_days, source
        )
        j = pd.DataFrame({"a": df1["close"], "b": df2["close"]}).dropna()
        if len(j) < 2:
            return json.dumps(
                {"status": "error", "message": "insufficient overlapping bars between symbols"},
                indent=2,
            )
        p = min(period, len(j))
        metrics = _bundle(
            j,
            [
                ("correlation_rolling", lambda: ta.correlation(j["a"], j["b"], p)),
                ("beta_rolling", lambda: ta.beta(j["a"], j["b"], p)),
                ("lrslope_symbol1", lambda: ta.lrslope(j["a"], p)),
            ],
        )
        metrics["pearson_full_sample"] = round(float(j["a"].corr(j["b"])), 4)
        return json.dumps(
            {
                "symbol1": symbol1.upper(),
                "symbol2": symbol2.upper(),
                "interval": interval,
                "period": p,
                "overlapping_bars": len(j),
                "metrics": metrics,
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        return f"Error calculating correlation/beta: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
