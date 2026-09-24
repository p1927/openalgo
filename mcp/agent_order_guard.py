"""Refuse raw order tools called from an autonomous agent's Vibe-Trading session.

Fork-owned sidecar, loaded by path from ``mcp/mcpserver.py``'s ``openalgo_tool`` decorator (same
trick and reason as ``mcp/custom_tools.py``: ``mcp/`` is not a package and its name collides with
the pip-installed ``mcp`` SDK). See ``docs/FORK_CONVENTIONS.md``.

An autonomous agent places orders only through ``execute_autonomous_basket`` and the ``submit_*``
bridge intents, which pass the one risk gate (Trade ADD autonomous_agents.md § Risk). The agent's
Vibe session never lists a raw order tool (Trade ``intent_capabilities.RAW_ORDER_TOOLS``); this is
the server-side backstop for when that filter is bypassed. Each raw order tool gains an optional
``vibe_session_id`` parameter, which the Vibe host fills in with its own session id
(``MCPRemoteTool._with_injected_session_id``). When that session belongs to an autonomous agent the
call is refused before any order is built. A human caller (a Vibe chat session no agent owns, or
any other MCP client, which sends no session id) is unaffected.

The same wrapper also turns every tool's error-shaped return into a real MCP tool error, so a
refusal (or any failure) reaches the client with ``isError: true`` instead of under a top-level ok.

ponytail: a model can pass a made-up ``vibe_session_id`` of its own and so slip past this check;
the registry filter is the primary gate. Upgrade: have the host overwrite the argument always.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import sys
from pathlib import Path

# The OpenAlgo members of Trade's intent_capabilities.RAW_ORDER_TOOLS (Trade's
# tests/test_agent_raw_order_tool_deny.py pins the two lists together).
RAW_ORDER_TOOLS = frozenset({
    "place_order", "place_smart_order", "place_basket_order", "place_split_order",
    "place_options_order", "place_options_multi_order", "modify_order", "cancel_order",
    "cancel_all_orders", "close_all_positions", "analyzer_toggle",
})


def _owning_agent_id(vibe_session_id: str) -> str | None:
    integrations = Path(__file__).resolve().parents[2] / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))
    os.environ.setdefault("TRADE_INTEGRATIONS_SKIP_APPLY", "1")
    from trade_integrations.autonomous_agents.store import list_agents

    for agent in list_agents():
        if str(agent.get("vibe_session_id") or "") == vibe_session_id:
            return str(agent.get("id"))
    return None


def _error_result_text(result):
    """The result text when ``result`` is an error-shaped tool return, else None.

    Tools report failure by *returning* ``"Error ..."`` prose, a JSON object whose ``status`` is
    ``"error"`` (the fork tools, OpenAlgo's own API answers), or upstream's ``_error`` shape
    ``{"error": {...}}``. Returned like that, FastMCP sends it as a successful result
    (``isError: false``), so a refused or failed call read as ``ok`` to the Vibe agent.
    """
    if not isinstance(result, str):
        return None
    if result.startswith("Error"):
        return result
    if not result.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(result)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    if status == "error" or (status in (None, "") and data.get("error")):
        return result
    return None


def _raise_on_error_result(fn):
    """Make an error-shaped return a real MCP tool error (``isError: true``), whose message is the
    tool's own error text, so the model reads exactly what it did before. Applied to every tool in
    the one place every tool is registered (``openalgo_tool``), not per tool. Trade backlog
    2026-09-23-mcp-tool-errors-reported-ok and 2026-09-23-mcp-wrapper-ok-on-tool-refusal."""
    from mcp.server.fastmcp.exceptions import ToolError

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        message = _error_result_text(result)
        if message is not None:
            raise ToolError(message)
        return result

    return wrapper


def guard(fn):
    """Wrap every tool so an error-shaped return is an MCP error, and a raw order tool with the
    agent-session refusal (whose refusal is itself an error-shaped return)."""
    return _raise_on_error_result(_refuse_for_agent_session(fn))


def _refuse_for_agent_session(fn):
    if fn.__name__ not in RAW_ORDER_TOOLS:
        return fn
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def guarded(*args, vibe_session_id: str = "", **kwargs):
        sid = str(vibe_session_id or "").strip()
        agent_id = _owning_agent_id(sid) if sid else None
        if agent_id:
            return json.dumps({"error": {
                "message": (
                    f"{fn.__name__} is not available to autonomous agent {agent_id}: an agent "
                    "places orders only through execute_autonomous_basket or the submit_* "
                    "bridge intents, which pass the risk gate."
                ),
                "code": "agent_raw_order_refused",
            }}, indent=2)
        return fn(*args, **kwargs)

    guarded.__signature__ = sig.replace(parameters=[
        *sig.parameters.values(),
        inspect.Parameter("vibe_session_id", inspect.Parameter.KEYWORD_ONLY, default="", annotation=str),
    ])
    return guarded
