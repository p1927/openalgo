"""Regression tests for utils/trade_api_key_sync.py's auto-generate-on-login behavior.

Covers the gap described in
.claude/backlog/items/2026-09-05-openalgo-auto-apikey-wiring.md: a successful
broker login previously only synced an *existing* API key into Trade's root
.env, and silently no-op'd if this user had never generated one. It should
now mint one first.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# trade_integrations.env is only reachable once ensure_trade_integrations_path()
# (called inside sync_api_key_to_trade) extends sys.path — but patch() resolves
# its target eagerly, before the function under test runs. Add the same path
# up front so `patch("trade_integrations.env...")` can resolve it.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integrations"))
# Same guard production's ensure_trade_integrations_path() sets before
# trade_integrations is ever imported: without it, importing the package
# eagerly patches tradingagents (langchain_core, etc.) which isn't installed
# in OpenAlgo's own isolated uv venv.
os.environ.setdefault("TRADE_INTEGRATIONS_SKIP_APPLY", "1")

import utils.trade_api_key_sync as trade_api_key_sync  # noqa: E402


def test_sync_generates_key_when_none_exists():
    """No existing key for the user -> one is generated, upserted, and synced."""
    with (
        patch("database.auth_db.get_api_key_for_tradingview", return_value=None) as get_key,
        patch("blueprints.apikey.generate_api_key", return_value="freshly-generated-key") as gen_key,
        patch("database.auth_db.upsert_api_key", return_value=42) as upsert_key,
        patch("trade_integrations.env.sync_openalgo_api_key", return_value=True) as sync_key,
    ):
        trade_api_key_sync.sync_api_key_to_trade("someuser")

    get_key.assert_called_once_with("someuser")
    gen_key.assert_called_once()
    upsert_key.assert_called_once_with("someuser", "freshly-generated-key")
    sync_key.assert_called_once_with("freshly-generated-key")


def test_sync_skips_generation_when_key_already_exists():
    """An existing key is synced as-is — never regenerated/rotated by a mere login."""
    with (
        patch("database.auth_db.get_api_key_for_tradingview", return_value="existing-key") as get_key,
        patch("blueprints.apikey.generate_api_key") as gen_key,
        patch("database.auth_db.upsert_api_key") as upsert_key,
        patch("trade_integrations.env.sync_openalgo_api_key", return_value=True) as sync_key,
    ):
        trade_api_key_sync.sync_api_key_to_trade("someuser")

    get_key.assert_called_once_with("someuser")
    gen_key.assert_not_called()
    upsert_key.assert_not_called()
    sync_key.assert_called_once_with("existing-key")


def test_sync_logs_and_returns_when_generation_fails():
    """upsert_api_key failing to persist the new key must not attempt to sync it."""
    with (
        patch("database.auth_db.get_api_key_for_tradingview", return_value=None),
        patch("blueprints.apikey.generate_api_key", return_value="freshly-generated-key"),
        patch("database.auth_db.upsert_api_key", return_value=None),
        patch("trade_integrations.env.sync_openalgo_api_key") as sync_key,
    ):
        trade_api_key_sync.sync_api_key_to_trade("someuser")

    sync_key.assert_not_called()


def test_sync_swallows_unexpected_exceptions():
    """Login itself must never break because this best-effort sync raised."""
    with patch(
        "broker.stock_simulator.api._trade_path.ensure_trade_integrations_path",
        side_effect=RuntimeError("boom"),
    ):
        trade_api_key_sync.sync_api_key_to_trade("someuser")  # must not raise
