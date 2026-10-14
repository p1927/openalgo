"""R9 exhaustive-read fixes in the Trade-owned fork code (one regression each)."""

from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_an_empty_registered_set_means_nothing_is_callable(monkeypatch):
    """A read-only boot that registers no tool must not read as "no filtering" (place_order stayed reachable)."""
    from utils import mcp_tool_registry as reg

    class _Module:
        ACTIVE_TOOL_NAMES = frozenset()

    monkeypatch.setattr(reg, "_load_mcpserver_module", lambda: _Module)
    assert reg.active_tool_names() == set()
    assert reg.list_tools_for_scopes(["orders:write", "orders:read"]) == []
    monkeypatch.setattr(reg, "_load_mcpserver_module", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert reg.active_tool_names() == set()  # a failed metadata read fails closed


def test_an_empty_env_value_does_not_read_the_next_line(tmp_path, monkeypatch):
    from utils import broker_env_sync as sync

    env = tmp_path / ".env"
    env.write_text("BROKER_API_SECRET=\nLOG_LEVEL=INFO\nBROKER_API_KEY = 'abc'\n", encoding="utf-8")
    monkeypatch.setattr(sync, "env_file_path", lambda: str(env))
    assert sync.read_env_key_from_file("BROKER_API_SECRET") == ""
    assert sync.read_env_key_from_file("BROKER_API_KEY") == "abc"


def test_the_json_line_formatter_redacts_a_traceback():
    from utils.logging import JSONLineFormatter

    try:
        raise RuntimeError("login failed api_key=SUPERSECRET123 password=hunter2")
    except RuntimeError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "msg", None, sys.exc_info())
    out = JSONLineFormatter().format(record)
    assert "SUPERSECRET123" not in out and "hunter2" not in out


def test_update_env_value_keeps_a_backslash_secret_verbatim():
    from blueprints.broker_credentials import update_env_value

    out = update_env_value("BROKER_API_SECRET = 'old'\n", "BROKER_API_SECRET", r"ab\dcd")
    assert r"ab\dcd" in out


def test_win_probability_covers_breakevens_beyond_a_narrow_drawn_grid():
    """A long 24500 CE at premium 60, spot 24250: the drawn grid 24000-24500 sits left of the breakeven 24560, and
    the score used to give the whole tail probability 0 (Monte Carlo reference 0.408)."""
    from services.strategy_synthesis.objective import score_combo
    from services.strategy_synthesis.payoff import SynthesizedLeg

    leg = SynthesizedLeg(strike=24500.0, option_type="CE", side="BUY", premium=60.0, qty=1)
    prices = np.linspace(24000.0, 24500.0, 51)
    scored = score_combo(
        prices, np.zeros_like(prices), [leg], 1, 0.0, 0.0, 0.0, 1.0, spot=24250.0, iv=0.2, years=0.1
    )
    assert 0.35 < scored.win_probability < 0.46
