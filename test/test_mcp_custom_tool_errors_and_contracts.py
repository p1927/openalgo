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


def _load():
    spec = importlib.util.spec_from_file_location("_custom_tools_errors_under_test", CUSTOM_TOOLS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _register_on(server: FastMCP):
    """register() the real custom tools onto a bare FastMCP, via a stub openalgo_tool."""
    module = _load()

    class _StubServer:
        RISK_BROKER_STRUCTURED = "broker_structured"
        RISK_EXTERNAL_TEXT = "external_text"

        @staticmethod
        def openalgo_tool(*_args, **_kwargs):
            return lambda fn: server.tool()(fn)

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
    module = _load()
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
    def fine() -> str:
        return json.dumps({"status": "ok", "note": "Error words inside a value are fine"})

    bad = _call(server, "json_error", {})
    assert bad.isError and "agent not found: aa_x" in bad.content[0].text
    prose = _call(server, "prose_error", {})
    assert prose.isError and "no Alpaca quote" in prose.content[0].text
    assert not _call(server, "fine", {}).isError


def test_register_wraps_every_custom_tool() -> None:
    server = FastMCP("t")
    module = _register_on(server)
    wrapper_code = module._raise_on_error_result(lambda: None).__code__
    tools = server._tool_manager.list_tools()
    assert len(tools) > 40
    unwrapped = [t.name for t in tools if t.fn.__code__ is not wrapper_code]
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
