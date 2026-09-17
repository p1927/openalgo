"""openalgo/.env must never win over the root repo .env for stock_simulator's replay knobs.

Regression: openalgo/.env held a stale NSE_REPLAY_SPEED=60 (a value D19 removed from the
allowed set) while the root .env correctly held 2. `utils/config.py` loaded the root .env
first with override=False, then openalgo's own .env with override=True -- so the stale
openalgo/.env value always won once both files defined the key. Nothing surfaced this until
trade_integrations.stock_simulator.config.load_sim_config() started being called from the
master-contract download path and raised ValueError. See Trade monorepo backlog item
2026-09-17-openalgo-own-env-nse-replay-speed-stale.md.

Uses a small local dotenv stand-in rather than the real `python-dotenv` loader: test/conftest.py
neutralises `dotenv.load_dotenv` repo-wide (`dotenv.load_dotenv = lambda *a, **kw: False`) so
tests never read an operator's real .env. The stand-in has the same override=True/False
semantics as python-dotenv for the plain `KEY=VALUE` lines these fixtures use, which is all
`utils.config._load_env_layers` needs to exercise the layering it does.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from utils import config as openalgo_config


def _fake_load_dotenv(dotenv_path=None, override=False) -> bool:
    for line in Path(dotenv_path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if override or key not in os.environ:
            os.environ[key] = value.strip()
    return True


@pytest.mark.unit
def test_openalgo_env_cannot_shadow_stale_sim_replay_speed(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NSE_REPLAY_SPEED", raising=False)
    monkeypatch.setattr(openalgo_config, "load_dotenv", _fake_load_dotenv)

    root_env = tmp_path / "root.env"
    root_env.write_text("NSE_REPLAY_SPEED=2\n")

    openalgo_env = tmp_path / "openalgo.env"
    openalgo_env.write_text("NSE_REPLAY_SPEED=60\n")

    openalgo_config._load_env_layers(root_env, str(openalgo_env))

    assert os.environ["NSE_REPLAY_SPEED"] == "2"

    # And the canonical reader agrees -- no ValueError from the now-disallowed 60x.
    from trade_integrations.stock_simulator.config import load_sim_config

    assert load_sim_config().speed == 2.0


@pytest.mark.unit
def test_openalgo_env_still_wins_for_non_sim_keys(tmp_path, monkeypatch) -> None:
    """The exemption is scoped to the sim-owned keys -- ordinary keys keep override=True."""
    monkeypatch.delenv("HOST_SERVER", raising=False)
    monkeypatch.setattr(openalgo_config, "load_dotenv", _fake_load_dotenv)

    root_env = tmp_path / "root.env"
    root_env.write_text("HOST_SERVER=http://root-value\n")

    openalgo_env = tmp_path / "openalgo.env"
    openalgo_env.write_text("HOST_SERVER=http://openalgo-value\n")

    openalgo_config._load_env_layers(root_env, str(openalgo_env))

    assert os.environ["HOST_SERVER"] == "http://openalgo-value"
