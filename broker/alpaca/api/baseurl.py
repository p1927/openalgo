"""Alpaca REST host selection — paper vs live from analyze_mode / ALPACA_PROFILE."""

from __future__ import annotations

import os

from database.settings_db import get_analyze_mode

PAPER_HOST = "https://paper-api.alpaca.markets"
LIVE_HOST = "https://api.alpaca.markets"
DATA_HOST = "https://data.alpaca.markets"


def _profile() -> str:
    profile = (os.getenv("ALPACA_PROFILE") or "paper").strip().lower()
    if profile not in ("paper", "live"):
        # Anything but "paper" used to mean the LIVE host, so a typo ("papper") sent orders to real money.
        raise ValueError(f"ALPACA_PROFILE must be 'paper' or 'live', got {profile!r}")
    return profile


def is_paper(analyze_mode: bool) -> bool:
    """The one paper-vs-live rule: paper when analyze mode is on or ALPACA_PROFILE=paper (the default)."""
    return bool(analyze_mode) or _profile() == "paper"


def is_paper_mode() -> bool:
    return is_paper(get_analyze_mode())


def trade_base_for(analyze_mode: bool) -> str:
    override = (os.getenv("ALPACA_API_BASE") or "").strip()
    if override:
        return override.rstrip("/")
    return PAPER_HOST if is_paper(analyze_mode) else LIVE_HOST


def trade_base() -> str:
    return trade_base_for(get_analyze_mode())


def data_base() -> str:
    return (os.getenv("ALPACA_DATA_BASE") or DATA_HOST).rstrip("/")


def data_feed() -> str:
    return (os.getenv("ALPACA_DATA_FEED") or "iex").strip().lower()
