"""Tool registry shim shared by stdio and HTTP transports.

stdio (legacy):
    The Claude Desktop / Cursor / Windsurf integration runs
    ``python mcp/mcpserver.py KEY HOST`` directly. FastMCP picks up
    ``@mcp.tool()`` decorators at import time and dispatches via stdio.

HTTP / SSE (new):
    ``blueprints/mcp_http.py`` imports this module after setting
    ``OPENALGO_MCP_HTTP_BOOT=1``. We expose:

    * ``TOOL_SCOPES`` — explicit map of tool_name → required OAuth scope.
      Maintained here (not derived from FastMCP) so security review can
      audit one place.
    * ``required_scope(name)`` — getter, returns ``None`` for unknown tools
    * ``list_tools_for_scopes(scopes)`` — filtered tool list for the
      ``tools/list`` JSON-RPC method
    * ``get_tool_callable(name)`` — resolves the underlying Python
      function so the dispatcher can call it directly. We do NOT round-
      trip through FastMCP's async layer — the SDK calls inside each
      tool are synchronous httpx calls and the eventlet worker handles
      them fine.

Drift check:
    ``audit_registry()`` walks FastMCP's internal tool list and warns
    about any tool that's missing from ``TOOL_SCOPES``. Logged at
    import time so a new tool added to ``mcp/mcpserver.py`` without a
    scope annotation surfaces in the boot log.
"""

from __future__ import annotations

from typing import Callable, Iterable

from utils.logging import get_logger

logger = get_logger(__name__)


# --------------------------------------------------------------------
# Scope catalogue. Three-way split per docs/prd/remote-mcp.md.
# --------------------------------------------------------------------
SCOPE_READ_MARKET = "read:market"
SCOPE_READ_ACCOUNT = "read:account"
SCOPE_WRITE_ORDERS = "write:orders"


# --------------------------------------------------------------------
# Explicit scope map — ONE source of truth. Adding a new MCP tool MUST
# add an entry here or it won't be reachable over the HTTP transport.
# audit_registry() warns about omissions at boot.
# --------------------------------------------------------------------
TOOL_SCOPES: dict[str, str] = {
    # ---- Order placement / modification / cancellation ----
    "place_order": SCOPE_WRITE_ORDERS,
    "place_smart_order": SCOPE_WRITE_ORDERS,
    "place_basket_order": SCOPE_WRITE_ORDERS,
    "place_split_order": SCOPE_WRITE_ORDERS,
    "place_options_order": SCOPE_WRITE_ORDERS,
    "place_options_multi_order": SCOPE_WRITE_ORDERS,
    "modify_order": SCOPE_WRITE_ORDERS,
    "cancel_order": SCOPE_WRITE_ORDERS,
    "cancel_all_orders": SCOPE_WRITE_ORDERS,
    "close_all_positions": SCOPE_WRITE_ORDERS,
    # analyzer_toggle flips between live and analyze (paper) modes — a
    # mistaken True silently routes future orders to the real broker.
    # Treated as a write because the blast radius is the same.
    "analyzer_toggle": SCOPE_WRITE_ORDERS,
    # ---- Autonomous agents (agent-driven) ----
    "stop_autonomous_agents": SCOPE_WRITE_ORDERS,
    "execute_autonomous_basket": SCOPE_WRITE_ORDERS,
    "get_autonomous_agent_status": SCOPE_READ_ACCOUNT,
    "get_autonomous_market_feedback": SCOPE_READ_ACCOUNT,
    # record_autonomous_decision mutates a *running* agent: it rewrites the
    # instance thesis (direction, strategy, confidence) the next agent turn
    # trades on, appends to the outcome ledger, and its EXIT branch is coupled
    # to real exit-order validation. Tightened from read:account, which let a
    # read-only token steer a live agent.
    "record_autonomous_decision": SCOPE_WRITE_ORDERS,
    # ---- Trade-stack research widgets (Vibe advisor) ----
    "get_options_browse": SCOPE_READ_MARKET,
    "get_options_trade_plan": SCOPE_READ_MARKET,
    "get_options_trade_widget": SCOPE_READ_MARKET,
    "get_stock_trade_plan": SCOPE_READ_MARKET,
    "get_stock_trade_widget": SCOPE_READ_MARKET,
    "get_index_trade_plan": SCOPE_READ_MARKET,
    "get_index_trade_widget": SCOPE_READ_MARKET,
    "get_plan_position_status": SCOPE_READ_ACCOUNT,
    "get_strategy_payoff": SCOPE_READ_MARKET,
    "get_trade_charges": SCOPE_READ_MARKET,
    "run_tradingagents_analysis": SCOPE_READ_MARKET,
    # ---- Account state ----
    "get_open_position": SCOPE_READ_ACCOUNT,
    "get_order_status": SCOPE_READ_ACCOUNT,
    "get_order_book": SCOPE_READ_ACCOUNT,
    "get_trade_book": SCOPE_READ_ACCOUNT,
    "get_position_book": SCOPE_READ_ACCOUNT,
    "get_holdings": SCOPE_READ_ACCOUNT,
    "get_funds": SCOPE_READ_ACCOUNT,
    "calculate_margin": SCOPE_READ_ACCOUNT,
    "analyzer_status": SCOPE_READ_ACCOUNT,
    # send_telegram_alert is account-scoped because the receiving channel
    # is the account owner's bot. No order placement, but it has a real
    # external side effect.
    "send_telegram_alert": SCOPE_READ_ACCOUNT,
    # ---- Market data ----
    "get_quote": SCOPE_READ_MARKET,
    "get_multi_quotes": SCOPE_READ_MARKET,
    "get_option_chain": SCOPE_READ_MARKET,
    "get_market_depth": SCOPE_READ_MARKET,
    "get_historical_data": SCOPE_READ_MARKET,
    "search_instruments": SCOPE_READ_MARKET,
    "get_symbol_info": SCOPE_READ_MARKET,
    "get_index_symbols": SCOPE_READ_MARKET,
    "get_expiry_dates": SCOPE_READ_MARKET,
    "get_available_intervals": SCOPE_READ_MARKET,
    "get_option_symbol": SCOPE_READ_MARKET,
    "get_synthetic_future": SCOPE_READ_MARKET,
    "get_option_greeks": SCOPE_READ_MARKET,
    "get_holidays": SCOPE_READ_MARKET,
    "get_timings": SCOPE_READ_MARKET,
    "check_holiday": SCOPE_READ_MARKET,
    "get_instruments": SCOPE_READ_MARKET,
    # ---- Research: technical indicators (openalgo.ta over history) ----
    "calculate_indicator": SCOPE_READ_MARKET,
    "get_trend_snapshot": SCOPE_READ_MARKET,
    "get_momentum_snapshot": SCOPE_READ_MARKET,
    "get_volatility_snapshot": SCOPE_READ_MARKET,
    "get_support_resistance": SCOPE_READ_MARKET,
    "detect_signals": SCOPE_READ_MARKET,
    "screen_instruments": SCOPE_READ_MARKET,
    "multi_timeframe_analysis": SCOPE_READ_MARKET,
    "correlation_beta": SCOPE_READ_MARKET,
    # ---- Info / introspection — readable by anyone with any scope ----
    # These are exempt from the scope filter because they help clients
    # discover what they can do. Implementing as read:market keeps the
    # check uniform without inventing a fourth scope.
    "get_openalgo_version": SCOPE_READ_MARKET,
    "validate_order_constants": SCOPE_READ_MARKET,
    # ================================================================
    # Fork-added tools, defined in mcp/custom_tools.py rather than
    # mcp/mcpserver.py. They are listed here, in the same dict, because
    # this module's whole point is that TOOL_SCOPES is ONE place a
    # security review can read -- a second, sidecar scope map would
    # defeat that even though it would merge more cleanly.
    #
    # The line drawn across these tools: anything that can change what a
    # live autonomous trading agent does with money -- dispatch an order,
    # halt a running agent, rewrite the rules or thesis it acts on -- is
    # write:orders and destructive, the same treatment place_order gets.
    # Everything that only reads market/account state or produces a
    # research artifact is read-scoped, even when it persists that
    # artifact to the local hub cache (a cache write is not broker state,
    # matching upstream's own read_only definition).
    # ================================================================
    # ---- Autonomous-agent order dispatch. Each of these reaches the
    # nautilus_openalgo_bridge intent queue and submit_intent() is
    # followed immediately by process_pending_intents(), so the order is
    # placed within the call -- not merely queued for later review.
    "submit_bridge_execution_intent": SCOPE_WRITE_ORDERS,
    "submit_partial_close": SCOPE_WRITE_ORDERS,
    "submit_hedge": SCOPE_WRITE_ORDERS,
    "submit_roll": SCOPE_WRITE_ORDERS,
    "submit_strike_roll": SCOPE_WRITE_ORDERS,
    # ---- Autonomous-agent control. No order leaves in the call itself,
    # but each one changes what a live agent will do next, so the blast
    # radius is the order path -- the same argument analyzer_toggle above
    # is scoped on.
    # propose_autonomous_agent persists a proposal that is one UI
    # confirmation away from a running trader; a read-only token must not
    # be able to plant one.
    "propose_autonomous_agent": SCOPE_WRITE_ORDERS,
    # set_agent_watch_spec rewrites a running agent's trigger rules and
    # re-syncs its handoff from the broker position book.
    "set_agent_watch_spec": SCOPE_WRITE_ORDERS,
    # create_session_watch / delete_watch add and remove live watches in
    # the shared registry. Deleting is the dangerous direction: the watch
    # removed may be the stop-level alert an open position is relying on.
    "create_session_watch": SCOPE_WRITE_ORDERS,
    "delete_watch": SCOPE_WRITE_ORDERS,
    # ---- Autonomous-agent / account state (read only) ----
    "list_watches": SCOPE_READ_ACCOUNT,
    "get_quant_monitor_status": SCOPE_READ_ACCOUNT,
    "get_portfolio_greeks": SCOPE_READ_ACCOUNT,
    "get_us_paper_account": SCOPE_READ_ACCOUNT,
    # market_context reports the active broker, analyzer/paper mode and
    # simulator replay state. It reads the toggle analyzer_toggle writes;
    # it cannot flip it.
    "market_context": SCOPE_READ_ACCOUNT,
    # ---- Market data ----
    "get_us_quote": SCOPE_READ_MARKET,
    "get_stock_browse": SCOPE_READ_MARKET,
    "get_hub_fii_dii": SCOPE_READ_MARKET,
    "get_hub_index_history": SCOPE_READ_MARKET,
    # ---- Research / hub reads. These read the trade-stack hub and may
    # refresh or persist its cached artifacts (parquet, widget JSON,
    # scenario drafts). Local cache writes only -- no broker state, no
    # money, no order -- so they stay read-scoped.
    "get_research_status": SCOPE_READ_MARKET,
    "get_hub_news": SCOPE_READ_MARKET,
    "run_quant_review": SCOPE_READ_MARKET,
    "get_pipeline_snapshot": SCOPE_READ_MARKET,
    "get_pipeline_news_items": SCOPE_READ_MARKET,
    "get_live_news_impact": SCOPE_READ_MARKET,
    "get_playground_context": SCOPE_READ_MARKET,
    "query_factor_explanation": SCOPE_READ_MARKET,
    "query_factor_sensitivity": SCOPE_READ_MARKET,
    "query_equation_coefficients": SCOPE_READ_MARKET,
    "query_constituent_drivers": SCOPE_READ_MARKET,
    "simulate_pipeline_scenario": SCOPE_READ_MARKET,
    "list_scenario_factors": SCOPE_READ_MARKET,
    "save_news_scenario_draft": SCOPE_READ_MARKET,
    "run_news_event_scenario": SCOPE_READ_MARKET,
    "get_news_scenario_widget": SCOPE_READ_MARKET,
    # ---- Browser-driven research. These drive a local headless browser
    # against public NSE/NSDL/web pages and write the parsed rows into the
    # hub. Outward network activity, but a fetch, not a send: nothing
    # account-identifying leaves and no broker state changes, so this is
    # the same class as get_historical_data reaching the broker.
    "get_nse_browser_status": SCOPE_READ_MARKET,
    "get_nse_browser_data": SCOPE_READ_MARKET,
    "run_nse_browser_mission": SCOPE_READ_MARKET,
    "run_browser_task": SCOPE_READ_MARKET,
    # ingest_nse_repository rebuilds the hub parquet cache from
    # git-tracked files already in the checkout. No fetch, no broker.
    "ingest_nse_repository": SCOPE_READ_MARKET,
}


# Tools that change something but are deliberately not write-scoped.
# Every entry is a tool a read-only token can still trigger, so each one
# needs a justification next to its TOOL_SCOPES entry above.
WRITE_SCOPE_EXCEPTIONS = {"send_telegram_alert"}


def required_scope(tool_name: str) -> str | None:
    """Return the scope required to call ``tool_name``, or None if unknown."""
    return TOOL_SCOPES.get(tool_name)


def active_tool_names() -> set[str] | None:
    """Tool names the MCP server actually registered this boot.

    ``mcp/mcpserver.py`` narrows its own registration from
    ``OPENALGO_MCP_TOOLSETS`` / ``OPENALGO_MCP_READ_ONLY``. The HTTP
    transport reads the result here so both transports advertise and
    accept the same set — an operator who boots read-only must not find
    order placement still reachable over HTTP.

    Returns None when the set cannot be determined, which callers treat
    as "no filtering" so a metadata failure never takes the transport
    down. ``tools/list`` did not previously load the tool module at all,
    so the load is guarded here rather than allowed to surface as a
    JSON-RPC error.
    """
    try:
        module = _load_mcpserver_module()
    except Exception:
        logger.exception("Could not load mcp/mcpserver.py to read active tools")
        return None
    names = getattr(module, "ACTIVE_TOOL_NAMES", None) if module else None
    if not names:
        return None
    return set(names)


def list_tools_for_scopes(granted_scopes: Iterable[str]) -> list[str]:
    """Tool names callable under at least one of the granted scopes.

    Intersected with the tools actually registered this boot.
    """
    granted = set(granted_scopes)
    active = active_tool_names()
    return sorted(
        name
        for name, scope in TOOL_SCOPES.items()
        if scope in granted and (active is None or name in active)
    )


def _load_mcpserver_module():
    """Load ``mcp/mcpserver.py`` directly by file path.

    The local ``mcp/`` directory is NOT a Python package (no
    ``__init__.py``) and the pip-installed ``mcp`` package shadows the
    name in normal imports. To reach our tool definitions we therefore
    resolve the file by path and load it through ``importlib.util``.
    Cached on the function attribute so repeat calls are free.
    """
    import importlib.util
    import os
    import sys

    cached = getattr(_load_mcpserver_module, "_module", None)
    if cached is not None:
        return cached

    # This file lives at <project>/utils/mcp_tool_registry.py; the MCP
    # entry point lives at <project>/mcp/mcpserver.py. Walk up + over.
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)
    target = os.path.join(project_root, "mcp", "mcpserver.py")
    spec = importlib.util.spec_from_file_location("openalgo_mcp_server", target)
    if spec is None or spec.loader is None:
        logger.error("Could not build spec for mcp/mcpserver.py")
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules["openalgo_mcp_server"] = module  # so its decorators bind
    spec.loader.exec_module(module)
    setattr(_load_mcpserver_module, "_module", module)
    return module


def get_tool_callable(tool_name: str) -> Callable | None:
    """Resolve the underlying Python function for a tool.

    The HTTP transport is responsible for setting
    ``OPENALGO_MCP_HTTP_BOOT=1`` before this module is loaded so the
    stdio argv check is bypassed.

    Returns None for a tool the server did not register this boot, so a
    narrowed server (read-only, or a subset of toolsets) refuses the
    call instead of running a tool it never advertised.
    """
    if tool_name not in TOOL_SCOPES:
        return None
    active = active_tool_names()
    if active is not None and tool_name not in active:
        return None
    module = _load_mcpserver_module()
    if module is None:
        return None
    fn = getattr(module, tool_name, None)
    return fn if callable(fn) else None


def registered_tool_names() -> set[str]:
    """Tool names FastMCP holds in its internal tool table.

    Best-effort — FastMCP's internal layout has shifted across versions,
    so multiple attribute paths are tried. An empty set means the layout
    was not recognised, not that there are no tools.
    """
    _mod = _load_mcpserver_module()
    if _mod is None:
        return set()

    fastmcp = getattr(_mod, "mcp", None)
    if fastmcp is None:
        return set()

    candidates = []
    for path in ("_tool_manager", "_tool_registry", "tools"):
        obj = getattr(fastmcp, path, None)
        if obj is None:
            continue
        # FastMCP often wraps tools in a manager that has a `_tools` dict
        for sub in ("_tools", "tools"):
            inner = getattr(obj, sub, None)
            if isinstance(inner, dict):
                candidates.append(inner)
        if isinstance(obj, dict):
            candidates.append(obj)

    seen: set[str] = set()
    for d in candidates:
        seen.update(d.keys())
    return seen


def audit_registry() -> None:
    """Log drift between mcp/mcpserver.py and this scope map.

    Three checks, all advisory — the HTTP transport still functions on
    TOOL_SCOPES alone, so a metadata mismatch degrades to a log line
    rather than a failed boot:

    1. A tool registered with FastMCP but absent from TOOL_SCOPES is
       unreachable over HTTP.
    2. A TOOL_SCOPES entry with no corresponding tool is dead config,
       usually a rename that only got applied on one side.
    3. A tool whose annotations disagree with its scope — a write tool
       filed under a read scope would be callable with a read-only
       token, so this is the one that actually matters for security.

    test/test_mcp_integrity.py asserts all three are clean, so in a
    healthy tree these never fire.
    """
    _mod = _load_mcpserver_module()
    if _mod is None:
        return

    seen = registered_tool_names()
    if not seen:
        return

    scoped = set(TOOL_SCOPES)

    missing = seen - scoped
    if missing:
        logger.warning(
            "MCP tools registered with FastMCP but missing TOOL_SCOPES "
            f"entries: {sorted(missing)}. They will not be reachable via "
            "the HTTP transport. Add them to utils/mcp_tool_registry.py."
        )

    # Only meaningful when the server booted unfiltered; a narrowed
    # server is expected to leave scope entries without a live tool.
    tool_meta = getattr(_mod, "TOOL_META", {}) or {}
    if tool_meta and len(seen) == len(tool_meta):
        orphaned = scoped - seen
        if orphaned:
            logger.warning(
                f"TOOL_SCOPES entries with no registered MCP tool: {sorted(orphaned)}. "
                "Stale after a rename or removal; drop them from "
                "utils/mcp_tool_registry.py."
            )

    for name, meta in tool_meta.items():
        scope = TOOL_SCOPES.get(name)
        if scope is None or name in WRITE_SCOPE_EXCEPTIONS:
            continue
        if not meta.read_only and scope != SCOPE_WRITE_ORDERS:
            logger.warning(
                f"MCP tool '{name}' is annotated as a write "
                f"(readOnlyHint=False) but carries scope '{scope}'. A read-only "
                "token could call it. Fix the scope in "
                "utils/mcp_tool_registry.py or the annotation in mcp/mcpserver.py."
            )
        elif meta.read_only and scope == SCOPE_WRITE_ORDERS:
            logger.warning(
                f"MCP tool '{name}' is annotated read-only but requires the "
                f"'{SCOPE_WRITE_ORDERS}' scope. Clients will be denied a call "
                "their annotations say is safe."
            )
