"""OpenAlgo-side adapter for the simulator-aware expiry list.

This file is the **only** module in the stock_simulator broker package
that knows the shape of an OpenAlgo expiry response. It is the bridge
between OpenAlgo's ``/search/api/expiries`` endpoint and the pure
data helper in ``integrations/trade_integrations/stock_simulator/options/expiry_query.py``.

Contract with the caller (``openalgo/blueprints/search.py::api_expiries``)
---------------------------------------------------------------------------
``get_expiries_override(underlying, exchange)`` returns:

* ``None``  -> "I have no opinion; please use the default SymToken path."
* ``dict``  -> ``{"status": "success", "expiries": [...], "source": "simulator_replay"}``
  or ``{"status": "success", ..., "source": "simulator_live"}``
* ``dict``  -> ``{"status": "error", "expiries": [], "source": "simulator_live_unavailable"}``
  when live mode's INDmoney fetch fails. This is intentionally NOT ``None``:
  in live mode the symtoken default path is guaranteed stale (frozen at the
  replay-anchored master-contract build), so we surface the failure instead
  of silently falling back to wrong dates.

Returning ``None`` instead of raising is the **removal property**: if this
file is deleted, the import in ``search.py`` fails, the surrounding
``try/except`` logs and falls through to the default path. Nothing else
in OpenAlgo ever imports this module.

Why this file exists
--------------------
When the active broker is ``stock_simulator`` and a replay is armed, the
master-contract ``symtoken`` table is stale: it was built at MC-build
time, anchored to the armed ``replay_date`` (the day the user *armed*,
not the day the replay *clock* is showing). If the user seeks the clock
forward into the future, past-expiry rows in the table should not be
in the dropdown any more, and future expiries that the bundle has but
the MC missed should be. This adapter answers that question against the
on-disk bundle instead of the table.

What this file is NOT
---------------------
* It does NOT mutate the master-contract ``symtoken`` table.
* It does NOT change anything about how the option-chain service
  fetches quotes — the chain service still uses the symtoken table for
  per-leg lookups. A separate follow-up (deliberately deferred) will
  lazily extend the MC when a user selects a chain whose (strike,
  expiry) is not yet in the table.
* It does NOT import from ``openalgo.blueprints.*`` or any other
  OpenAlgo blueprint / service. The only OpenAlgo-side surface it
  touches is the well-known ``replay`` singleton and the path helpers
  in ``trade_integrations``.

How to REMOVE this feature
--------------------------
1. Delete this file.
2. Delete ``integrations/trade_integrations/stock_simulator/options/expiry_query.py``.
3. Delete the bracketed simulator-aware block in
   ``openalgo/blueprints/search.py::api_expiries``.

Nothing else in the codebase imports from this module — the only caller
is the ``search.py`` block referenced above.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _is_master_contract_ready() -> bool:
    """Return True only when the stock_simulator master contract is ready.

    The override should not advertise expiries the chain call cannot
    honour. The master contract is what the chain service consults
    for per-strike and per-expiry lookups, so if it is mid-download
    or in an error state, the override opts out and the default
    symtoken path runs. Without this guard the dropdown would show
    bundle-driven expiries that 404 when the user picks them.

    Defensive on every level: any failure (DB down, import error,
    status not yet written) returns ``False`` so the override never
    returns a stale-or-incomplete list. The chain call fallback to
    the symtoken table is the safer degraded state than a dropdown
    full of broken choices.
    """
    try:
        from database.master_contract_status_db import get_status
    except Exception:
        logger.exception(
            "simulator expiry override: MC status import failed; treating as not ready"
        )
        return False
    try:
        status = get_status("stock_simulator")
    except Exception:
        logger.exception(
            "simulator expiry override: MC status read failed; treating as not ready"
        )
        return False
    if not status:
        # No status row at all — first arm before the MC has ever
        # run. Opt out so the user sees the (empty) symtoken list
        # rather than a half-built dropdown.
        return False
    # is_ready is the project's source of truth for "the symtoken
    # table is populated enough to service option-chain calls". The
    # status field is informational but is_ready is the bit the
    # rest of OpenAlgo actually checks (see check_if_ready).
    return bool(status.get("is_ready"))


_LIVE_EXPIRIES_UNAVAILABLE: dict[str, Any] = {
    "status": "error",
    "message": (
        "Live expiries are unavailable right now (simulator is in live mode "
        "and the INDmoney F&O master could not be reached). Not falling back "
        "to the replay-anchored symtoken table — those dates would be stale."
    ),
    "expiries": [],
    "source": "simulator_live_unavailable",
}


def _live_expiries_override(underlying: str) -> dict[str, Any]:
    """Live-mode counterpart of the replay-bundle path below.

    Queries INDmoney's real F&O scrip master (via
    ``LiveQuoteService.list_expiries``, which shares the recorder's
    ``IndClient``/rate limiter) for ``underlying``'s live expiries.

    Deliberately does NOT return ``None`` on failure. ``None`` means
    "opt out, use the default symtoken path" — but in live mode the
    symtoken table is only ever refreshed by the replay-anchored
    master-contract build, so it is guaranteed stale (wrong dates, not
    just missing ones). Falling back to it here would silently show
    the user expiries that don't exist any more. Instead, any failure
    or empty result returns an explicit error payload so the caller
    can surface "expiries unavailable" rather than wrong dates.
    """
    try:
        from broker.stock_simulator.api._trade_path import (
            ensure_trade_integrations_path,
        )

        ensure_trade_integrations_path()

        from trade_integrations.stock_simulator.live_quotes import get_live_quote_service
    except Exception:
        logger.exception(
            "simulator expiry override: live import/setup failed; "
            "returning unavailable (not falling back to stale symtoken table)"
        )
        return dict(_LIVE_EXPIRIES_UNAVAILABLE)

    try:
        expiries = get_live_quote_service().list_expiries(underlying)
    except Exception:
        logger.exception(
            "simulator expiry override: live expiry fetch failed; "
            "returning unavailable (not falling back to stale symtoken table)"
        )
        return dict(_LIVE_EXPIRIES_UNAVAILABLE)

    if not expiries:
        logger.warning(
            "simulator expiry override: live expiry fetch returned no expiries "
            "for %s; returning unavailable (not falling back to stale symtoken table)",
            underlying,
        )
        return dict(_LIVE_EXPIRIES_UNAVAILABLE)

    return {
        "status": "success",
        "expiries": expiries,
        "source": "simulator_live",
    }


def get_expiries_override(underlying: str, exchange: str) -> dict[str, Any] | None:
    """Return a simulator-aware expiry list, or ``None`` to opt out.

    Args:
        underlying: Underlying symbol as the user sees it (e.g. ``NIFTY``).
        exchange: Options exchange as the user sees it (e.g. ``NFO``).
            Currently unused — the bundle layout is keyed by index
            exchange internally, not options exchange. Kept in the
            signature so the caller does not have to change if a future
            bundle variant wants options-exchange discrimination.

    Returns:
        ``None`` when:
          * the replay service cannot be loaded (no env config, no
            bundle on disk, import error inside the simulator package),
          * the underlying does not map to a known bundle slug,
          * the master contract is not in a ready state (mid-download
            or error) — see ``_is_master_contract_ready``.
          These are the cases where the default symtoken path is a
          *reasonable* answer (possibly empty, but not wrong).
        A dict with ``status: "error"`` and ``source:
        "simulator_live_unavailable"`` when no replay date is armed
        (live mode) and the live INDmoney expiry fetch fails or
        returns nothing — deliberately NOT ``None``, because falling
        through to the symtoken table here would show stale dates,
        not just missing ones. See ``_live_expiries_override``.
        Otherwise a dict matching OpenAlgo's expiry endpoint response,
        augmented with ``source: "simulator_replay"`` or
        ``source: "simulator_live"`` so the frontend can label the
        dropdown as simulator-driven.
    """
    del exchange  # See docstring.

    try:
        # Import lazily so a missing/broken simulator package never
        # blocks the import of this module.
        from broker.stock_simulator.api._trade_path import (
            ensure_trade_integrations_path,
        )

        ensure_trade_integrations_path()

        from trade_integrations.stock_simulator.options.expiry_query import (
            get_expiries_for_sim_now,
        )
        from trade_integrations.stock_simulator.replay import get_replay_service
    except Exception:
        logger.exception("simulator expiry override: import/setup failed; opting out")
        return None

    try:
        service = get_replay_service()
    except Exception:
        logger.exception("simulator expiry override: replay service unavailable; opting out")
        return None

    replay_date = getattr(getattr(service, "config", None), "replay_date", None)
    if not replay_date:
        # No replay armed: the simulator is in live mode. The symtoken
        # table is only ever refreshed by the replay-anchored
        # master-contract build, so it has no reliable answer for
        # today's live expiries — ask INDmoney directly instead of
        # opting out into a stale dropdown. Always returns a dict
        # (success or explicit error), never None, so this never
        # falls through to the stale symtoken path.
        return _live_expiries_override(underlying)

    try:
        sim_now = service.sim_now()
    except Exception:
        logger.exception("simulator expiry override: sim_now raised; opting out")
        return None

    try:
        expiries = get_expiries_for_sim_now(
            data_root=service.config.data_root,
            underlying=underlying,
            # The helper's ``exchange`` parameter is reserved for future
            # per-options-exchange bundle support; today the bundle layout
            # is keyed by index exchange, so we pass a placeholder. The
            # helper ignores the value.
            exchange="NSE_INDEX",
            sim_now=sim_now,
        )
    except Exception:
        logger.exception("simulator expiry override: bundle scan failed; opting out")
        return None

    # Guard against showing expiries the symtoken table cannot service.
    # Done LAST so a transient MC rebuild that completes between this
    # call and the previous one still has a chance of being picked up.
    if not _is_master_contract_ready():
        logger.info(
            "simulator expiry override: MC not ready; opting out so dropdown "
            "shows symtoken list instead of bundle-driven expiries"
        )
        return None

    return {
        "status": "success",
        "expiries": expiries,
        "source": "simulator_replay",
    }
