"""The propose tool's `allowed_instruments` schema must be an unambiguous array of known values.

Live 2026-09-07, a real orchestrator turn sent `"allowed_instruments": {"item": "options"}`:
a model shown a bare `list[str] | None` schema produced the array's item as an object. Pydantic
rejected it, the model then dropped the field entirely, and the value was guessed. A `Literal`
item type puts the allowed values into the schema as an enum, and the docstring shows the array
literally. Checked from the AST, like test_mcp_custom_tools_import_guard.py, so the test needs no
running MCP server.

See Trade's .claude/backlog/items/2026-09-07-mcp-propose-allowed-instruments-schema-mismatch.md.

Run: uv run pytest test/test_mcp_propose_allowed_instruments_schema.py -v
"""

from __future__ import annotations

import ast
from pathlib import Path

CUSTOM_TOOLS = Path(__file__).resolve().parent.parent / "mcp" / "custom_tools.py"


def _propose_tool() -> ast.FunctionDef:
    tree = ast.parse(CUSTOM_TOOLS.read_text(encoding="utf-8"))
    return next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "propose_autonomous_agent"
    )


def test_allowed_instruments_is_a_list_of_the_known_literals() -> None:
    fn = _propose_tool()
    arg = next(a for a in fn.args.args if a.arg == "allowed_instruments")

    assert ast.unparse(arg.annotation) == "list[Literal['equity', 'options', 'futures']] | None"


def test_the_docstring_shows_the_array_shape_literally() -> None:
    doc = ast.get_docstring(_propose_tool()) or ""

    assert '["options"]' in doc
    assert "Not an object" in doc
