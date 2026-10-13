"""One resolver for "which .env does this process use" (OPENALGO_ENV_FILE), D306.

Six places used to re-implement the override; one forgot it and a scratch instance could not start.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from utils import broker_env_sync, config as openalgo_config
from utils.env_check import env_file_path

ROOT = Path(__file__).resolve().parents[1]


def test_override_wins_and_is_absolute(monkeypatch, tmp_path):
    f = tmp_path / "scratch.env"
    monkeypatch.setenv("OPENALGO_ENV_FILE", str(f))
    assert env_file_path() == str(f)
    monkeypatch.setenv("OPENALGO_ENV_FILE", "  rel/x.env  ")
    assert env_file_path() == os.path.abspath("rel/x.env")


def test_blank_override_falls_back_to_checkout_env(monkeypatch):
    monkeypatch.setenv("OPENALGO_ENV_FILE", "   ")
    assert env_file_path() == str(ROOT / ".env")
    monkeypatch.delenv("OPENALGO_ENV_FILE")
    assert env_file_path() == str(ROOT / ".env")


def test_every_caller_follows_the_override(monkeypatch, tmp_path):
    f = tmp_path / "scratch.env"
    f.write_text("BROKER_API_KEY=abc\n")
    monkeypatch.setenv("OPENALGO_ENV_FILE", str(f))
    from blueprints import broker_credentials

    assert broker_credentials.env_file_path() == str(f)
    assert broker_env_sync.env_file_path() == str(f)
    assert openalgo_config.env_file_path() == str(f)
    text, err = broker_credentials.read_env_file()
    assert err is None and "BROKER_API_KEY=abc" in text


def test_no_second_resolver_left():
    """The override is read in exactly one source file."""
    hits = []
    for p in ROOT.rglob("*.py"):
        parts = p.relative_to(ROOT).parts
        if parts[0] in {"test", "tests", "node_modules", ".venv", "frontend", "upgrade"}:
            continue
        if re.search(r"""getenv\(\s*["']OPENALGO_ENV_FILE|environ(?:\.get|\[)\(?\s*["']OPENALGO_ENV_FILE""", p.read_text(errors="ignore")):
            hits.append(str(p.relative_to(ROOT)))
    assert hits == ["utils/env_check.py"], hits
