"""Ensure trade_integrations is importable from OpenAlgo process.

Replay mode/arm state is owned by the stock_simulator service's tier-state lease (Trade D375); this
process keeps no copy of it in env or `sandbox_db`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

def ensure_trade_integrations_path() -> None:
    os.environ.setdefault("TRADE_INTEGRATIONS_SKIP_APPLY", "1")
    trade_root = Path(__file__).resolve().parents[4]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))
    # trade_integrations transitively imports tradingagents (e.g. dataflows.news_aggregator);
    # without this on sys.path those imports raise ModuleNotFoundError inside the OpenAlgo
    # process, which source_availability misreads as repeated OpenAlgo quote failures and
    # trips the circuit breaker.
    tradingagents = trade_root / "tradingagents"
    if tradingagents.is_dir() and str(tradingagents) not in sys.path:
        sys.path.insert(0, str(tradingagents))
