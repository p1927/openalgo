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
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")


def _broker_is_stock_simulator(api_key: str | None) -> bool:
    if os.getenv("STOCK_SIMULATOR_MODE", "").strip().lower() == "replay":
        return True
    from database.auth_db import get_broker_name, get_first_available_api_key

    from flask import has_request_context, session

    if has_request_context() and session.get("broker") == "stock_simulator":
        return True
    # No key and no browser session (the sandbox's own position/expiry sweeps): the
    # single-user install's logged-in broker decides.
    key = api_key or get_first_available_api_key()
    return bool(key) and get_broker_name(key) == "stock_simulator"


def simulator_now() -> datetime:
    """The IST instant the stock_simulator service's clock is on. Raises if the service cannot
    be asked: a stale guess here serves expired contracts as live, or settles live ones."""
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.stock_simulator.client import StockSimulatorClient
    from trade_integrations.stock_simulator.run_identity import simulator_now as _from_status

    return _from_status(StockSimulatorClient().status())


def expiry_reference_now(api_key: str | None = None) -> datetime:
    """IST-aware "now" for contract expiry: the wall clock for live brokers, the simulator's
    own instant for stock_simulator. The sandbox settled contracts that were live on the
    replayed day because it compared their expiry against the wall clock
    (Trade backlog 2026-09-23-entry-fills-expired-contract).

    ponytail: memoised for 1 s — the sandbox asks once per position/order in a loop, and each
    answer costs a DB key lookup plus a simulator HTTP call; 1 s staleness cannot move a
    contract across its expiry in any way that matters.
    """
    cached = _NOW_CACHE.get(api_key)
    if cached is not None and time.monotonic() - cached[0] < 1.0:
        return cached[1] + timedelta(seconds=time.monotonic() - cached[0])
    now = _expiry_reference_now_uncached(api_key)
    _NOW_CACHE[api_key] = (time.monotonic(), now)
    return now


_NOW_CACHE: dict[str | None, tuple[float, datetime]] = {}


def _expiry_reference_now_uncached(api_key: str | None) -> datetime:
    try:
        from broker.stock_simulator.api._trade_path import hydrate_simulator_env_from_db

        hydrate_simulator_env_from_db()
    except Exception:
        pass

    if _broker_is_stock_simulator(api_key):
        return simulator_now()
    return datetime.now(_IST)


def expiry_reference_date(api_key: str | None) -> date:
    """Wall-clock today (IST) for live brokers; the simulator's own day for stock_simulator."""
    return expiry_reference_now(api_key).date()
