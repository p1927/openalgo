"""Shared master-contract rebuild-on-date-change trigger.

Used by ``blueprints/simulator_control.py`` (Sandbox UI) after it learns a new replay
date/window from the standalone stock_simulator service. A replay armed on the service
directly is caught by ``mc_freshness`` at the next login/master-contract check instead.
"""

from __future__ import annotations

from threading import Thread


def replay_date_of(status: dict) -> str:
    """``YYYY-MM-DD`` the simulator clock is parked on, from a ``/control/replay/status`` payload.

    The clock always carries one (live or replay), so a missing key is a bad payload and raises
    ``KeyError`` instead of being papered over with an invented date.
    """
    return status["clock"]["replay_date"][:10]


def trigger_mc_rebuild_if_date_changed(
    prior_replay_date: str, new_replay_date: str, *, force: bool = False
) -> str | None:
    """Kick off an async master-contract rebuild if the replay date actually
    changed (or ``force=True``, e.g. a week-mode toggle with the same date).
    Returns ``"completed"``/``"started"``/``None`` (no rebuild needed).

    The symtoken table (built by ``master_contract_download``) is anchored to
    whichever day was last armed — without a rebuild, the option-chain
    endpoint 404s on any (strike, expiry) the new day introduced.
    """
    if not force and (not prior_replay_date or not new_replay_date or new_replay_date == prior_replay_date):
        return None

    from utils.auth_utils import async_master_contract_download

    rebuild_thread = Thread(target=async_master_contract_download, args=("stock_simulator",), daemon=True)
    rebuild_thread.start()
    # Bounded wait so the response the frontend acts on (e.g. immediately
    # re-fetching /search/api/expiries to populate the dropdown) doesn't race
    # the rebuild. Normally sub-second; if it somehow runs long we still
    # return rather than block the request indefinitely.
    rebuild_thread.join(timeout=8.0)
    return "completed" if not rebuild_thread.is_alive() else "started"
