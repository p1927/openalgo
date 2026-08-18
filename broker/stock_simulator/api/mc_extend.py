"""Lazy master-contract extension for the stock_simulator broker.

Why this file exists
--------------------
The option-chain service consults the ``symtoken`` master-contract
table for per-strike and per-expiry lookups via
``get_available_strikes``. When the symtoken table is missing rows
for the user's selected (base_symbol, expiry), the chain call 404s
even though the on-disk HF replay bundle has the data.

This module is the last-resort safety net for that case: when the
chain call lands and the symtoken table has no rows for the
requested expiry, scan the bundle's per-expiry parquet and
insert the missing (strike, expiry) rows. Idempotent — safe to
call multiple times, no-ops if the rows are already there.

What this file is NOT
---------------------
* It does NOT replace ``master_contract_download()``. The full MC
  build is still the right path when the user arms a new replay
  day; this helper only fills in the gap when one expiry was
  missed (a transient race or a user-selected chain that the
  bulk build hadn't covered).
* It does NOT mutate the on-disk bundle. The bundle is the
  source of truth; the symtoken table is the cache.
* It does NOT import Flask or any OpenAlgo blueprint. The chain
  service is the only caller.

How to REMOVE this feature
--------------------------
1. Delete this file.
2. Delete the bracketed lazy-extension block in
   ``openalgo/services/option_chain_service.get_option_chain``
   (just before the "No strikes found" 404 return).
3. Delete ``openalgo/test/test_simulator_lazy_mc_extend.py``.

Nothing else in the codebase imports from this module.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_expiry_iso(expiry_date: str) -> str | None:
    """Convert an OpenAlgo-format expiry (``DD-MMM-YY``) to ``YYYY-MM-DD``.

    Returns ``None`` if the input is not parseable; the caller
    treats that as "no matching file on disk" and no-ops.
    """
    from datetime import date, datetime

    raw = expiry_date.strip().upper()
    for fmt in ("%d-%b-%y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(raw, fmt).date()
            return d.isoformat()
        except ValueError:
            continue
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def extend_master_contract_for_expiry(
    *,
    base_symbol: str,
    expiry_date: str,
    options_exchange: str,
    replay_service: Any,
) -> int:
    """Insert (strike, expiry) rows into symtoken for a single expiry.

    Reads the per-expiry parquet from the on-disk HF replay bundle
    and inserts one symtoken row per (strike, CE|PE) leg. Idempotent:
    existing rows for the same (name, exchange, expiry) are
    deleted first, so the table ends up exactly mirroring the
    parquet's content for that expiry.

    Args:
        base_symbol: The underlying as the user sees it (``NIFTY``,
            ``BANKNIFTY``, ``SENSEX``). Must be one of the known
            index aliases; anything else returns 0 silently.
        expiry_date: Expiry in either ``DD-MMM-YY`` or ``YYYY-MM-DD``
            format. ``expiry_date`` here matches what the option-chain
            service receives from the request (``DD-MMM-YY``) and
            what the symtoken table stores.
        options_exchange: The options exchange to insert rows for
            (``NFO`` for NIFTY/BANKNIFTY, ``BFO`` for SENSEX).
        replay_service: The simulator's ``ReplayService`` singleton.
            Used for ``config.data_root`` and the underlying
            metadata. The function only reads from it; no mutation.

    Returns:
        Number of symtoken rows inserted (0 if the underlying is
        not a known index alias, the parquet file does not exist,
        the file is empty, or the database write fails).
    """
    try:
        from trade_integrations.stock_simulator.hf_paths import (
            index_slug,
            options_dir,
        )
    except Exception:
        logger.exception(
            "lazy MC extend: import of trade_integrations paths failed; opting out"
        )
        return 0

    # Resolve underlying to the bundle slug (NSE_INDEX first, then BSE_INDEX).
    slug = index_slug(base_symbol, "NSE_INDEX") or index_slug(
        base_symbol, "BSE_INDEX"
    )
    if not slug:
        return 0

    expiry_iso = _resolve_expiry_iso(expiry_date)
    if not expiry_iso:
        return 0

    data_root = getattr(getattr(replay_service, "config", None), "data_root", None)
    if data_root is None:
        return 0

    bundle_file = options_dir(data_root, slug) / f"{expiry_iso}.parquet"
    if not bundle_file.is_file():
        return 0

    try:
        import pandas as pd
        raw = pd.read_parquet(
            bundle_file, columns=["strike", "option_type"]
        )
    except Exception:
        logger.exception(
            "lazy MC extend: failed to read bundle parquet %s; opting out",
            bundle_file,
        )
        return 0

    if raw.empty:
        return 0

    legs: set[tuple[float, str]] = set()
    for _, row in raw.drop_duplicates(subset=["strike", "option_type"]).iterrows():
        strike = float(row["strike"])
        opt_type = str(row["option_type"]).upper()
        if opt_type in {"CE", "PE"}:
            legs.add((strike, opt_type))

    if not legs:
        return 0

    # Build the symtoken rows. Mirror ``_option_rows_for_underlying``
    # in trade_integrations so the inserted rows are bit-for-bit
    # compatible with what the bulk MC build produces.
    try:
        from trade_integrations.stock_simulator.config import load_sim_config
        from trade_integrations.stock_simulator.master_contract import (
            openalgo_option_symbol,
            synthetic_token,
        )
        from trade_integrations.stock_simulator.master_contract import (
            format_expiry_column,
        )
    except Exception:
        logger.exception(
            "lazy MC extend: import of trade_integrations helpers failed; opting out"
        )
        return 0

    cfg = load_sim_config()
    lotsize_raw = cfg.SIM_MC_LOTSIZE if hasattr(cfg, "SIM_MC_LOTSIZE") else 25
    lotsize = int(lotsize_raw) if str(lotsize_raw).isdigit() else 25
    tick_size = 0.05  # NIFTY/BANKNIFTY/SENSEX all use 0.05.

    from datetime import date as _date
    expiry_d = _date.fromisoformat(expiry_iso)
    expiry_col = format_expiry_column(expiry_d)
    brexchange = "NSE" if options_exchange.upper() == "NFO" else "BSE"

    rows: list[dict[str, Any]] = []
    for strike, opt_type in legs:
        sym = openalgo_option_symbol(slug, expiry_d, strike, opt_type)
        rows.append(
            {
                "symbol": sym,
                "brsymbol": sym,
                "name": slug,
                "exchange": options_exchange,
                "brexchange": brexchange,
                "token": synthetic_token(sym, options_exchange),
                "expiry": expiry_col,
                "strike": float(strike),
                "lotsize": lotsize,
                "instrumenttype": opt_type,
                "tick_size": tick_size,
            }
        )

    # Database write: delete existing rows for this (name, exchange,
    # expiry), then insert the fresh set in a single transaction.
    # Idempotent — a second call is a no-op once the rows match.
    try:
        from database.symbol import SymToken, db_session
    except Exception:
        logger.exception(
            "lazy MC extend: import of database.symbol failed; opting out"
        )
        return 0

    try:
        db_session.query(SymToken).filter(
            SymToken.name == slug,
            SymToken.exchange == options_exchange,
            SymToken.expiry == expiry_col,
        ).delete(synchronize_session=False)
        db_session.bulk_insert_mappings(SymToken, rows)
        db_session.commit()
    except Exception:
        logger.exception(
            "lazy MC extend: symtoken write failed for %s expiring %s",
            slug,
            expiry_col,
        )
        try:
            db_session.rollback()
        except Exception:
            pass
        return 0

    logger.info(
        "lazy MC extend: inserted %d symtoken rows for %s expiring %s on %s",
        len(rows),
        slug,
        expiry_col,
        options_exchange,
    )
    return len(rows)
