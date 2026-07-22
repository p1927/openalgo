"""Ensure trade_integrations is importable from OpenAlgo process."""

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
