"""Expiry reference date for the stock_simulator broker.

Why this file exists
---------------------
``services.expiry_service.get_expiry_dates`` filters out expired
contracts by comparing each expiry against "today". For a real broker
that's just wall-clock today; for stock_simulator it must be the day the
simulator's own clock is on, or a replay of a past trading day loses
every expiry before the current calendar date. Keeping that
broker-specific date resolution here, rather than inline in
expiry_service.py, keeps the shared expiry-filtering logic broker-agnostic.

The simulator's clock is read from the stock_simulator service itself
(``/control/replay/status``), never from ``NSE_REPLAY_DATE``: that value
is whatever replay date was last persisted in the sandbox DB, and it went
stale while the service replayed a different day (dev: anchor 2026-08-21
while the service replayed 2026-09-22, so the nearest "live" NIFTY expiry
came back as 25-AUG-26).

To remove stock_simulator support: delete this file and the
``expiry_reference_date`` call site in expiry_service.py (revert it
to ``datetime.now().date()``).
"""

import os
from datetime import date, datetime


def _broker_is_stock_simulator(api_key: str | None) -> bool:
    if os.getenv("STOCK_SIMULATOR_MODE", "").strip().lower() == "replay":
        return True
    if api_key:
        from database.auth_db import get_broker_name

        if get_broker_name(api_key) == "stock_simulator":
            return True
    from flask import has_request_context, session

    return has_request_context() and session.get("broker") == "stock_simulator"


def simulator_today() -> date:
    """The day the stock_simulator service's clock is on. Raises if the service cannot be
    asked: a stale guess here serves expired contracts as live."""
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.stock_simulator.client import StockSimulatorClient
    from trade_integrations.stock_simulator.run_identity import simulator_day

    return simulator_day(StockSimulatorClient().status())


def expiry_reference_date(api_key: str | None) -> date:
    """Wall-clock today for live brokers; the simulator's own day for stock_simulator."""
    try:
        from broker.stock_simulator.api._trade_path import hydrate_simulator_env_from_db

        hydrate_simulator_env_from_db()
    except Exception:
        pass

    if _broker_is_stock_simulator(api_key):
        return simulator_today()
    return datetime.now().date()
