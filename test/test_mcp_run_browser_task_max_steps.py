"""The MCP run_browser_task tool must advertise the step ceiling the runner actually enforces.

It used to default max_steps to 50 while its own docstring said 1-20, and the runner in the
parent Trade repo (trade_integrations.nse_browser.agent_runner.MAX_BROWSER_TASK_STEPS) caps
at 20. An LLM picks its budget from this schema. Checked from the AST, like the other
custom_tools tests, so no MCP server or Trade import is needed.

See Trade's .claude/backlog/items/2026-09-10-browser-task-max-steps-range.md.

Run: uv run pytest test/test_mcp_run_browser_task_max_steps.py -v
"""

from __future__ import annotations

import ast
from pathlib import Path

CUSTOM_TOOLS = Path(__file__).resolve().parent.parent / "mcp" / "custom_tools.py"
RUNNER_CEILING = 20  # trade_integrations.nse_browser.agent_runner.MAX_BROWSER_TASK_STEPS


def _tool() -> ast.FunctionDef:
    tree = ast.parse(CUSTOM_TOOLS.read_text(encoding="utf-8"))
    return next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_browser_task"
    )


def test_max_steps_defaults_to_the_runner_ceiling() -> None:
    fn = _tool()
    names = [a.arg for a in fn.args.args]
    defaults = dict(zip(names[len(names) - len(fn.args.defaults):], fn.args.defaults))

    assert ast.literal_eval(defaults["max_steps"]) == RUNNER_CEILING


def test_the_docstring_states_the_range_and_the_cap() -> None:
    doc = ast.get_docstring(_tool()) or ""

    assert "1-20" in doc
    assert "max_steps_effective" in doc
