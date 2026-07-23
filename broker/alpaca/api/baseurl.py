"""Alpaca REST host selection — paper vs live from analyze_mode / ALPACA_PROFILE."""

from __future__ import annotations

import os

from database.settings_db import get_analyze_mode

PAPER_HOST = "https://paper-api.alpaca.markets"
LIVE_HOST = "https://api.alpaca.markets"
DATA_HOST = "https://data.alpaca.markets"


def _profile() -> str:
    return (os.getenv("ALPACA_PROFILE") or "paper").strip().lower()


def is_paper_mode() -> bool:
    """Paper trading API when analyze_mode is on or ALPACA_PROFILE=paper."""
    if get_analyze_mode():
        return True
    return _profile() == "paper"


def trade_base() -> str:
    override = (os.getenv("ALPACA_API_BASE") or "").strip()
    if override:
        return override.rstrip("/")
    return PAPER_HOST if is_paper_mode() else LIVE_HOST


def data_base() -> str:
    return (os.getenv("ALPACA_DATA_BASE") or DATA_HOST).rstrip("/")


def data_feed() -> str:
    return (os.getenv("ALPACA_DATA_FEED") or "iex").strip().lower()
