"""The one rule for "does analyze mode send this call to OpenAlgo's sandbox?" (fork sidecar).

Analyze mode normally routes every order/position/funds call to the local sandbox. A broker with
its own paper venue (Alpaca: ``paper-api.alpaca.markets``) is the exception: analyze mode there
means "use the broker's paper account", so the call stays on the broker plugin. Services route on
``get_analyze_mode() and not has_own_paper_venue(broker)``; the broker rule lives here once so
they cannot drift apart (D306).
"""

from __future__ import annotations

# Brokers whose analyze mode is the broker's own paper account, not OpenAlgo's sandbox.
OWN_PAPER_BROKERS = frozenset({"alpaca"})


def has_own_paper_venue(broker: str | None) -> bool:
    return (broker or "").strip().lower() in OWN_PAPER_BROKERS
