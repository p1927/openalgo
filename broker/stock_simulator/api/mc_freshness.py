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

from database.master_contract_status_db import get_status


def should_download_stock_simulator_contract() -> tuple[bool, str]:
    """Return (should_download, reason) for the stock_simulator broker.

    Compares the cached master-contract fingerprint (replay date, underlying universe, max
    expiries, recorded equities, stock-option symbols) against ``mc_cache_fingerprint`` for the
    replay date ``load_sim_config()`` resolves — the exact call ``master_contract_download``
    stores — rather than using a daily-cutoff rule that assumes a real broker feed. The replay
    date is never read from ``NSE_REPLAY_DATE``: under week-rotation that key is unset (a
    hardcoded default here made every check report a changed date and rebuild every time).
    """
    broker_status = get_status("stock_simulator")
    stats = broker_status.get("exchange_stats") or {}
    if broker_status.get("status") == "error":
        return True, "Simulator master contract last download failed"
    from database.token_db import get_symbol_count

    if get_symbol_count() == 0:
        return True, "Simulator symtoken table is empty"

    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.stock_simulator.config import load_sim_config
    from trade_integrations.stock_simulator.master_contract import mc_cache_fingerprint

    cfg = load_sim_config()
    wanted = mc_cache_fingerprint(replay_date=cfg.replay_date, data_root=cfg.data_root)
    if stats.get("replay_date") != wanted["replay_date"]:
        return True, f"Simulator replay date changed to {wanted['replay_date']}"
    for key, label in (
        ("underlyings", "underlying universe"),
        ("max_expiries", "max expiries"),
        ("equities", "recorded equities"),
        ("stock_options", "stock-option symbols"),
    ):
        cached = stats.get(key)
        if cached is None:
            return True, f"Simulator {label} not cached"
        if isinstance(cached, list):
            cached = sorted(cached)
        expected = sorted(wanted[key]) if isinstance(wanted[key], list) else wanted[key]
        if cached != expected:
            return True, f"Simulator {label} changed"
    return False, "Simulator master contract matches replay fingerprint"
