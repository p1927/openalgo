"""Master-contract freshness check for the stock_simulator broker.

Why this file exists
---------------------
``utils.auth_utils.should_download_master_contract`` decides whether to
re-download a broker's master contract based on a daily cutoff time —
that rule doesn't apply to stock_simulator, which has no real broker to
poll and instead needs to detect whether its *replay fingerprint*
(replay date, underlying universe, max expiries, recorded equities) has
drifted from what's already cached. Keeping that broker-specific check
here, rather than inline in auth_utils.py, means the shared master
contract logic doesn't accumulate simulator-specific branches.

To remove stock_simulator support: delete this file and the
``if broker == "stock_simulator":`` call site in auth_utils.py.
"""

import os

from database.master_contract_status_db import get_status


def should_download_stock_simulator_contract() -> tuple[bool, str]:
    """Return (should_download, reason) for the stock_simulator broker.

    Compares the cached master-contract fingerprint (replay date,
    underlying universe, max expiries, recorded equities) against what
    the current sim config / replay bundle would produce, rather than
    using a daily-cutoff rule that assumes a real broker feed.
    """
    broker_status = get_status("stock_simulator")
    stats = broker_status.get("exchange_stats") or {}
    if broker_status.get("status") == "error":
        return True, "Simulator master contract last download failed"
    try:
        from database.token_db import get_symbol_count

        if get_symbol_count() == 0:
            return True, "Simulator symtoken table is empty"
    except Exception:
        pass
    replay_date = os.getenv("NSE_REPLAY_DATE", "2021-03-25").strip()[:10]
    if stats.get("replay_date") != replay_date:
        return True, f"Simulator replay date changed to {replay_date}"
    try:
        from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

        ensure_trade_integrations_path()
        from trade_integrations.stock_simulator.config import load_sim_config
        from trade_integrations.stock_simulator.master_contract import (
            load_mc_equities,
            load_mc_max_expiries,
            load_mc_underlyings,
        )

        wanted = load_mc_underlyings()
        max_expiries_wanted = load_mc_max_expiries()
        equities_wanted = load_mc_equities(load_sim_config().data_root)
    except Exception:
        wanted = [
            u.strip().upper()
            for u in os.getenv("SIM_MC_UNDERLYINGS", "NIFTY,BANKNIFTY,SENSEX").split(",")
            if u.strip()
        ]
        try:
            max_expiries_wanted = int(os.getenv("SIM_MC_MAX_EXPIRIES", "12") or "12")
        except ValueError:
            max_expiries_wanted = 12
        equities_wanted = []
    cached = stats.get("underlyings")
    if wanted:
        if not cached:
            return True, "Simulator underlying universe not cached"
        if sorted(wanted) != sorted(cached):
            return True, "Simulator underlying universe changed"
    cached_max = stats.get("max_expiries")
    if cached_max is None:
        return True, "Simulator max expiries not cached"
    if cached_max != max_expiries_wanted:
        return True, "Simulator max expiries changed"
    cached_equities = sorted(stats.get("equities") or [])
    if sorted(equities_wanted) != cached_equities:
        return True, "Simulator recorded equities changed"
    return False, "Simulator master contract matches replay fingerprint"
