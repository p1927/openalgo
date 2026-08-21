"""Expiry reference date for the stock_simulator broker.

Why this file exists
---------------------
``services.expiry_service.get_expiry_dates`` filters out expired
contracts by comparing each expiry against "today". For a real broker
that's just wall-clock today; for stock_simulator running in replay
mode it must be the replay anchor date instead, or every expiry before
the *current* calendar date gets filtered out even though the replay
is showing a past trading day. Keeping that broker-specific date
resolution here, rather than inline in expiry_service.py, keeps the
shared expiry-filtering logic broker-agnostic.

To remove stock_simulator support: delete this file and the
``expiry_reference_date`` call site in expiry_service.py (revert it
to ``datetime.now().date()``).
"""

import os
from datetime import date, datetime


def expiry_reference_date(api_key: str | None) -> date:
    """Wall-clock today for live brokers; replay anchor date for stock_simulator."""
    try:
        from broker.stock_simulator.api._trade_path import hydrate_simulator_env_from_db

        hydrate_simulator_env_from_db()
    except Exception:
        pass

    def _replay_anchor() -> date | None:
        replay = os.getenv("NSE_REPLAY_DATE", "2021-03-25").strip()[:10]
        try:
            return date.fromisoformat(replay)
        except ValueError:
            return None

    if os.getenv("STOCK_SIMULATOR_MODE", "").strip().lower() == "replay":
        anchor = _replay_anchor()
        if anchor is not None:
            return anchor

    if api_key:
        from database.auth_db import get_broker_name

        if get_broker_name(api_key) == "stock_simulator":
            anchor = _replay_anchor()
            if anchor is not None:
                return anchor

    try:
        from flask import has_request_context, session

        if has_request_context() and session.get("broker") == "stock_simulator":
            anchor = _replay_anchor()
            if anchor is not None:
                return anchor
    except Exception:
        pass

    return datetime.now().date()
