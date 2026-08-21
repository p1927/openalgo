"""Fork-only extensions to ``services/option_chain_service.py`` (an upstream file).

Kept in a sidecar module so upstream's ``get_option_chain`` only carries a
couple of single-line calls into here, instead of this logic being spliced
into its body — see openalgo `CLAUDE.md` invariant on the stock_simulator
broker for why the lazy master-contract extension only ever applies to it.
"""

from datetime import datetime
from typing import Callable


def normalize_expiry_to_ddmmmyy(raw: str | None) -> str | None:
    """Coerce caller-supplied expiry strings to the ``DDMMMYY`` contract
    that ``option_symbol_service.get_available_strikes`` (and the symtoken
    table) actually use.

    Accepts the shapes the rest of the API surface emits:
        * ``DD-MMM-YY``     (e.g. ``25-AUG-26``)
        * ``DD-MMM-YYYY``   (e.g. ``25-AUG-2026``)
        * ``DDMMMYY``       (e.g. ``25AUG26``)
        * ``YYYY-MM-DD``    (e.g. ``2026-08-25``)

    Returns ``raw`` unchanged when no format matches — better to let the
    downstream query raise a clear "no such expiry" error than to silently
    mangle the input.
    """
    if not raw:
        return raw
    cleaned = raw.strip().upper()
    for fmt in ("%d%b%y", "%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%d%b%y").upper()
        except ValueError:
            continue
    return raw


def try_extend_master_contract_and_retry(
    *,
    active_broker: str | None,
    base_symbol: str,
    final_expiry: str,
    options_exchange: str,
    logger,
    get_available_strikes: Callable[[str, str, str, str], list],
) -> list:
    """Retry ``get_available_strikes`` after a lazy master-contract extension.

    The symtoken table may be missing rows for this (underlying, expiry)
    pair. For real brokers this is a real 404 — the broker doesn't have the
    contract. For stock_simulator the on-disk HF replay bundle may have the
    data even though the bulk MC build hasn't covered it yet (e.g. the user
    is mid-replay-arm and the async MC rebuild thread hasn't completed, or
    the bundle was updated after the last MC build). Try to extend the MC
    lazily from the bundle; if it works, the chain call can proceed.

    No-op (returns an empty list) for every broker other than
    stock_simulator, and swallows its own failures — the caller falls
    through to its normal "no strikes" 404 either way.
    """
    if active_broker != "stock_simulator":
        return []
    try:
        from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

        ensure_trade_integrations_path()

        from broker.stock_simulator.api.mc_extend import extend_master_contract_for_expiry
        from trade_integrations.stock_simulator.replay import get_replay_service

        replay_service = get_replay_service()
        extend_master_contract_for_expiry(
            base_symbol=base_symbol,
            expiry_date=final_expiry,
            options_exchange=options_exchange,
            replay_service=replay_service,
        )
        # The strikes cache in option_symbol_service holds the empty result
        # from the first call above. Without invalidation, the retry would
        # just read the cached [] and the lazy extension would be a no-op.
        # ``clear_strikes_cache`` is the documented entry point for "master
        # contract was updated" — see option_symbol_service.py:70.
        from services.option_symbol_service import clear_strikes_cache

        clear_strikes_cache()
        return get_available_strikes(base_symbol, final_expiry, "CE", options_exchange)
    except Exception:
        logger.exception(
            "lazy MC extension failed for %s expiring %s; falling through to 404",
            base_symbol,
            final_expiry,
        )
        return []
