"""Custom MCP tools: error returns are real MCP errors, and argument types match the docstrings.

* A tool that *returns* ``"Error ..."`` or ``{"status": "error"}`` used to go out as a successful
  result (``isError: false``), so the vibe agent's tool trail counted failures as ``ok``. Every
  custom tool is now wrapped so such a return raises, and FastMCP reports ``isError: true``.
  (Trade backlog 2026-09-23-mcp-tool-errors-reported-ok)
* ``run_browser_task`` declared ``start_urls`` / ``output_schema`` as ``str`` while its docstring
  asked for a JSON array/object, so the model's list was rejected by pydantic.
  (Trade backlog 2026-09-23-mcp-tool-arg-contracts)

Run: uv run pytest test/test_mcp_custom_tool_errors_and_contracts.py -v
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

CUSTOM_TOOLS = Path(__file__).resolve().parent.parent / "mcp" / "custom_tools.py"
TOOL_GUARD = Path(__file__).resolve().parent.parent / "mcp" / "agent_order_guard.py"


def _load(path=CUSTOM_TOOLS, name="_custom_tools_errors_under_test"):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_guard():
    """The sidecar openalgo_tool applies to every tool (error returns -> MCP errors)."""
    return _load(TOOL_GUARD, "_tool_guard_under_test")


def _register_on(server: FastMCP):
    """register() the real custom tools onto a bare FastMCP, via a stub openalgo_tool."""
    module = _load()
    guard = _load_guard()

    class _StubServer:
        RISK_BROKER_STRUCTURED = "broker_structured"
        RISK_EXTERNAL_TEXT = "external_text"

        @staticmethod
        def openalgo_tool(*_args, **_kwargs):
            # As the real openalgo_tool does: every tool goes through agent_order_guard.guard.
            return lambda fn: server.tool()(guard.guard(fn))

        def __getattr__(self, _name):
            return "stub"

    module.register(_StubServer())
    return module


def _call(server: FastMCP, name: str, arguments: dict):
    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return await client.call_tool(name, arguments)

    return anyio.run(run)


def test_error_shaped_returns_become_mcp_errors() -> None:
    module = _load_guard()
    server = FastMCP("t")

    @server.tool()
    @module._raise_on_error_result
    def json_error() -> str:
        return json.dumps({"status": "error", "error": "agent not found: aa_x"})

    @server.tool()
    @module._raise_on_error_result
    def prose_error() -> str:
        return "Error: no Alpaca quote available for XYZ"

    @server.tool()
    @module._raise_on_error_result
    def upstream_error() -> str:
        # upstream mcpserver._error's shape, and the agent raw-order refusal's
        # (Trade backlog 2026-09-23-mcp-wrapper-ok-on-tool-refusal)
        return json.dumps({"error": {"message": "refused", "code": "agent_raw_order_refused"}})

    @server.tool()
    @module._raise_on_error_result
    def fine() -> str:
        return json.dumps({"status": "ok", "note": "Error words inside a value are fine"})

    bad = _call(server, "json_error", {})
    assert bad.isError and "agent not found: aa_x" in bad.content[0].text
    prose = _call(server, "prose_error", {})
    assert prose.isError and "no Alpaca quote" in prose.content[0].text
    upstream = _call(server, "upstream_error", {})
    assert upstream.isError and "agent_raw_order_refused" in upstream.content[0].text
    assert not _call(server, "fine", {}).isError


def test_register_wraps_every_custom_tool() -> None:
    server = FastMCP("t")
    _register_on(server)
    wrapper_code = _load_guard()._raise_on_error_result(lambda: None).__code__
    tools = server._tool_manager.list_tools()
    assert len(tools) > 40
    # Same function in agent_order_guard.py (each load of the file makes its own code object).
    same = lambda code: (code.co_filename, code.co_qualname) == (wrapper_code.co_filename, wrapper_code.co_qualname)
    unwrapped = [t.name for t in tools if not same(t.fn.__code__)]
    assert unwrapped == []


def test_run_browser_task_schema_takes_array_and_object() -> None:
    server = FastMCP("t")
    _register_on(server)
    tool = server._tool_manager.get_tool("run_browser_task")
    props = tool.parameters["properties"]
    assert {"type": "array", "items": {"type": "string"}} in props["start_urls"]["anyOf"]
    assert any(b.get("type") == "object" for b in props["output_schema"]["anyOf"])

    # The docstring's own example validates, and so does a legacy JSON-string argument.
    meta = tool.fn_metadata
    for args in (
        {"goal": "g", "start_urls": ["https://www.rbi.org.in/"], "output_schema": {"type": "object"}},
        {"goal": "g", "start_urls": '["https://www.rbi.org.in/"]'},
    ):
        parsed = meta.arg_model.model_validate(meta.pre_parse_json(args))
        assert parsed.start_urls == ["https://www.rbi.org.in/"]


def test_record_autonomous_decision_actions_taken_is_a_string_array() -> None:
    server = FastMCP("t")
    _register_on(server)
    props = server._tool_manager.get_tool("record_autonomous_decision").parameters["properties"]
    assert {"type": "array", "items": {"type": "string"}} in props["actions_taken"]["anyOf"]


def test_in_app_http_transport_does_not_serve_the_trade_tools(monkeypatch) -> None:
    """Trade DECISIONS D349 (Trade backlog 2026-09-23-openalgo-http-mcp-lacks-trade-deps): inside
    the broker app (OPENALGO_MCP_IN_APP=1, openalgo/.venv) the Trade tools are defined but
    unregistered, so an HTTP call fails with "Tool not implemented"; stdio still serves them."""
    from types import SimpleNamespace

    monkeypatch.setenv("OPENALGO_MCP_IN_APP", "1")
    server = FastMCP("t")
    module = _load()
    tool_meta: dict = {}

    class _StubServer:
        RISK_BROKER_STRUCTURED = "broker_structured"
        RISK_EXTERNAL_TEXT = "external_text"
        TOOL_META = tool_meta
        mcp = server

        @staticmethod
        def openalgo_tool(*_args, **_kwargs):
            def register(fn):
                tool_meta[fn.__name__] = SimpleNamespace(registered=True)
                return server.tool()(fn)

            return register

        def __getattr__(self, _name):
            return "stub"

    module.register(_StubServer())
    assert len(tool_meta) > 40
    assert server._tool_manager.list_tools() == []
    assert not any(meta.registered for meta in tool_meta.values())
