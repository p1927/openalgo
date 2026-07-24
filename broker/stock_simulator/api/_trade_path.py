"""Ensure trade_integrations is importable from OpenAlgo process."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_hydrated = False

_SIM_DB_ENV: tuple[tuple[str, str], ...] = (
    ("sim_replay_date", "NSE_REPLAY_DATE"),
    ("sim_replay_time", "NSE_REPLAY_TIME"),
    ("sim_replay_speed", "NSE_REPLAY_SPEED"),
    ("sim_replay_loop", "NSE_REPLAY_LOOP"),
    ("sim_eval_mode", "SIM_EVAL_MODE"),
    ("sim_week_mode", "NSE_REPLAY_WEEK_MODE"),
    ("sim_week_days_count", "NSE_REPLAY_WEEK_COUNT"),
)


def hydrate_simulator_env_from_db() -> None:
    """Apply UI-persisted simulator settings to process env (survives restart)."""
    global _hydrated
    if _hydrated:
        return
    try:
        from database.sandbox_db import get_config

        applied = False
        db_has_sim_keys = False
        for db_key, env_key in _SIM_DB_ENV:
            val = get_config(db_key)
            if val is not None:
                db_has_sim_keys = True
            if val is not None and str(val).strip() != "":
                os.environ[env_key] = str(val)
                applied = True
        if applied:
            os.environ.setdefault("STOCK_SIMULATOR_MODE", "replay")
            os.environ.setdefault("HUB_NO_LEARN", "1")
        if applied or not db_has_sim_keys:
            _hydrated = True
    except Exception:
        pass


def ensure_trade_integrations_path() -> None:
    hydrate_simulator_env_from_db()
    os.environ.setdefault("TRADE_INTEGRATIONS_SKIP_APPLY", "1")
    trade_root = Path(__file__).resolve().parents[4]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))
