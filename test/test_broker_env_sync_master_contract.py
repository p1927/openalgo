"""An env-token broker's master contract must be checked on startup, not only on login.

Regression: `sync_env_token_brokers_on_startup` wrote the broker's token straight into the
auth DB and never called anything from `auth_utils` -- the function that actually checks
whether a master contract needs downloading only ran from the interactive OAuth callback
(`handle_auth_success`). Several env-token brokers, `stock_simulator` included, have a no-op
login (see `broker/stock_simulator/api/auth_api.py`), so that callback never fires for them:
the auth DB could hold a live-looking token indefinitely with an empty symtoken table. See
Trade monorepo backlog item 2026-08-28-openalgo-fno-eligibility-registry-empty.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils import broker_env_sync


@pytest.mark.unit
def test_startup_sync_checks_master_contract_when_a_target_user_exists(monkeypatch) -> None:
    monkeypatch.setattr(broker_env_sync, "get_configured_broker", lambda: "stock_simulator")
    monkeypatch.setattr(
        "utils.broker_credentials.apply_broker_credentials", lambda _broker: None
    )
    monkeypatch.setattr(
        broker_env_sync,
        "sync_env_secret_to_auth_db",
        lambda reload_env=True: {
            "synced": False,
            "reason": "already_in_sync",
            "broker": "stock_simulator",
            "updated_users": [],
        },
    )
    ensure_master_contract = MagicMock()
    monkeypatch.setattr("utils.auth_utils.ensure_master_contract", ensure_master_contract)

    broker_env_sync.sync_env_token_brokers_on_startup()

    ensure_master_contract.assert_called_once_with("stock_simulator")


@pytest.mark.unit
def test_startup_sync_skips_master_contract_check_with_no_target_user(monkeypatch) -> None:
    """Nothing to check yet on a genuinely fresh install with no user row at all."""
    monkeypatch.setattr(broker_env_sync, "get_configured_broker", lambda: "stock_simulator")
    monkeypatch.setattr(
        "utils.broker_credentials.apply_broker_credentials", lambda _broker: None
    )
    monkeypatch.setattr(
        broker_env_sync,
        "sync_env_secret_to_auth_db",
        lambda reload_env=True: {
            "synced": False,
            "reason": "no_target_user",
            "broker": "stock_simulator",
            "updated_users": [],
        },
    )
    ensure_master_contract = MagicMock()
    monkeypatch.setattr("utils.auth_utils.ensure_master_contract", ensure_master_contract)

    broker_env_sync.sync_env_token_brokers_on_startup()

    ensure_master_contract.assert_not_called()


@pytest.mark.unit
def test_startup_sync_never_raises_when_ensure_master_contract_fails(monkeypatch) -> None:
    """`sync_env_token_brokers_on_startup` is documented as never raising -- startup must
    proceed even if this new call blows up."""
    monkeypatch.setattr(broker_env_sync, "get_configured_broker", lambda: "stock_simulator")
    monkeypatch.setattr(
        "utils.broker_credentials.apply_broker_credentials", lambda _broker: None
    )
    monkeypatch.setattr(
        broker_env_sync,
        "sync_env_secret_to_auth_db",
        lambda reload_env=True: {
            "synced": True,
            "reason": "updated",
            "broker": "stock_simulator",
            "updated_users": ["admin"],
        },
    )

    def _boom(_broker):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("utils.auth_utils.ensure_master_contract", _boom)

    broker_env_sync.sync_env_token_brokers_on_startup()  # must not raise
