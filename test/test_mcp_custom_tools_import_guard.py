"""Every fork MCP tool that imports ``trade_integrations`` must find it.

``mcp/custom_tools.py``'s tools import ``trade_integrations`` from the parent
Trade repo. The MCP server runs under ``openalgo/.venv``, which has no
``trade_stack`` editable ``.pth``, so the import only resolves after
``_ensure_trade_stack_import()`` has put ``<repo>/integrations`` and
``<repo>/tradingagents`` on ``sys.path``. 19 tools used to import without it:
whichever ran first in a cold process failed, and the failure was swallowed
into an error string. ``register()`` now calls the guard once, up front.

See Trade's .claude/backlog/items/2026-09-10-mcp-unguarded-stack-import.md.

Run: uv run pytest test/test_mcp_custom_tools_import_guard.py -v
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

CUSTOM_TOOLS = Path(__file__).resolve().parent.parent / "mcp" / "custom_tools.py"
GUARD = "_ensure_trade_stack_import"


def _register_node() -> ast.FunctionDef:
    tree = ast.parse(CUSTOM_TOOLS.read_text(encoding="utf-8"))
    return next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "register"
    )


def _calls(node: ast.AST) -> set[str]:
    return {
        c.func.id
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }


def _register_guards_up_front(reg: ast.FunctionDef) -> bool:
    return any(
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and getattr(stmt.value.func, "id", "") == GUARD
        for stmt in reg.body
    )


def test_every_trade_integrations_import_is_guarded():
    """Either register() runs the guard before any tool exists, or every tool
    that imports trade_integrations calls it (directly or via an _import_* helper)."""
    reg = _register_node()
    if _register_guards_up_front(reg):
        return
    unguarded = []
    for fn in reg.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        imports = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("trade_integrations")
        ]
        if not imports:
            continue
        called = _calls(fn)
        if GUARD in called or any(name.startswith("_import_") for name in called):
            continue
        unguarded.append(f"{fn.name} (line {fn.lineno})")
    assert not unguarded, f"tools import trade_integrations without {GUARD}(): {unguarded}"


def test_register_puts_trade_stack_on_sys_path(monkeypatch):
    """Behavioural half: register() alone (no tool invoked) makes the parent
    repo's integrations/ importable, which is what a cold MCP process needs."""
    trade_root = CUSTOM_TOOLS.resolve().parents[2]
    integrations = trade_root / "integrations"
    if not integrations.is_dir():
        import pytest

        pytest.skip("openalgo is not checked out inside the Trade repo")

    stripped = [p for p in sys.path if Path(p).resolve() != integrations.resolve()]
    monkeypatch.setattr(sys, "path", stripped)
    monkeypatch.delenv("TRADE_INTEGRATIONS_SKIP_APPLY", raising=False)

    class _StubServer:
        RISK_BROKER_STRUCTURED = "broker_structured"
        RISK_EXTERNAL_TEXT = "external_text"

        @staticmethod
        def openalgo_tool(*_args, **_kwargs):
            return lambda fn: fn

        def __getattr__(self, _name):
            return "stub"

    spec = importlib.util.spec_from_file_location("_custom_tools_under_test", CUSTOM_TOOLS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.register(_StubServer())

    assert str(integrations) in sys.path
