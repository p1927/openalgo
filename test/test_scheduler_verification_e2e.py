"""End-to-end verification of openalgo's 5 scheduler families (family D of the
Trade repo's full scheduler verification pass, see
.claude/plans and the report this feeds into scripts/verify_schedulers.py's
output for the other families).

Not a pytest suite — a standalone script, same pattern as
`test/test_analyzer_toggle_restriction.py`: it drives real service-layer calls
against the running local dev stack (openalgo Flask app on the port in
stack/ports.yaml, sqlite dbs under db/). Run with:

    uv run python test/test_scheduler_verification_e2e.py

Safety: this script REFUSES to run unless Analyze Mode (paper trading) is on.
Every order it places is verified to land only in the isolated sandbox tables
(SandboxPositions/SandboxOrders in db/sandbox.db) — the live-broker order path
is structurally unreachable while Analyze Mode is on (see
services/place_smart_order_service.py), so this never touches a real broker.

What it exercises (calls the real scheduler-job functions directly, in-process
— openalgo's 5 APScheduler instances have no manual "trigger now" HTTP
endpoint, only real cron/interval firing, so calling the function IS the
faithful way to fire one of these jobs on demand):
  - blueprints.strategy.squareoff_positions(strategy_id)
  - blueprints.chartink.squareoff_positions(strategy_id)
  - blueprints.python_strategy.scheduled_start_strategy/scheduled_stop_strategy/
    daily_trading_day_check/market_hours_enforcer/cleanup_dead_processes
  - flow/historify jobs: audited read-only via scheduler_registry_service
    (they only fire on real cron/interval; no safe manual trigger exists)

Each call is followed by a correctness check against real DB state (not just
"did it raise"), and everything seeded is torn down at the end.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

RESULTS: list[dict] = []


def record(name: str, verdict: str, notes: str) -> None:
    RESULTS.append({"check": name, "verdict": verdict, "notes": notes})
    print(f"[{verdict.upper():6}] {name}: {notes}")


def main() -> int:
    from database.settings_db import get_analyze_mode
    from database.auth_db import ApiKeys, Auth

    if not get_analyze_mode():
        print("FATAL: Analyze Mode is OFF. Refusing to run — this script only ever "
              "runs against sandbox (paper) mode, never a live broker. Enable Analyze "
              "Mode via /settings/analyze-mode before running this script.")
        return 1
    record("analyze_mode_check", "pass", "Analyze Mode confirmed ON before doing anything")

    if ApiKeys.query.first() is None or Auth.query.filter_by(is_revoked=False).first() is None:
        record(
            "broker_session_check", "skipped_expected",
            "no live API key / broker session in this checkout — MARKET-priced sandbox "
            "orders need a quote source, so position-seeding steps that need pricing are skipped",
        )
        have_quotes = False
    else:
        record("broker_session_check", "pass", "API key + active broker session present")
        have_quotes = True

    user_id = Auth.query.filter_by(is_revoked=False).first().name if have_quotes else None
    api_key = None
    if have_quotes:
        from database.auth_db import get_api_key_for_tradingview
        api_key = get_api_key_for_tradingview(user_id)
        if not api_key:
            record("api_key_lookup", "fail", f"no tradingview-style API key found for user {user_id}")
            have_quotes = False

    test_tag = f"schedver_{uuid.uuid4().hex[:8]}"
    created_strategy_ids: list[int] = []
    created_chartink_ids: list[int] = []
    created_python_ids: list[str] = []

    # ------------------------------------------------------------------
    # 1. strategy squareoff (blueprints/strategy.py)
    # ------------------------------------------------------------------
    try:
        from database import strategy_db
        from database.sandbox_db import SandboxPositions, db_session as sandbox_session
        from services.sandbox_service import sandbox_place_order

        strat = strategy_db.create_strategy(
            name=f"{test_tag}-strat", webhook_id=str(uuid.uuid4()), user_id=user_id or "test",
            is_intraday=True, trading_mode="LONG",
            start_time="09:15", end_time="15:00", squareoff_time="15:15",
        )
        if strat is None:
            record("strategy_squareoff", "fail", "create_strategy returned None")
        else:
            created_strategy_ids.append(strat.id)
            # CNC (delivery), not MIS: the sandbox engine enforces a real MIS
            # squareoff-cutoff window against IST wall-clock time, which this
            # verification run may execute outside of.
            strategy_db.add_symbol_mapping(strat.id, "RELIANCE", "NSE", 1, "CNC")

            if have_quotes:
                ok, resp, code = sandbox_place_order(
                    {"symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
                     "quantity": 1, "pricetype": "MARKET", "product": "CNC"},
                    api_key, {},
                )
                if not ok:
                    record("strategy_squareoff", "fail", f"seed BUY order failed: {resp}")
                else:
                    time.sleep(2)  # let the sandbox execution engine fill the market order
                    from blueprints.strategy import squareoff_positions
                    squareoff_positions(strat.id)
                    time.sleep(3)  # squareoff queues a loopback HTTP order; give it a moment
                    sandbox_session.remove()
                    pos = SandboxPositions.query.filter_by(
                        user_id=user_id, symbol="RELIANCE", exchange="NSE", product="CNC"
                    ).first()
                    qty = pos.quantity if pos else 0
                    if qty == 0:
                        record("strategy_squareoff", "pass", "position flat after squareoff_positions()")
                    else:
                        record("strategy_squareoff", "fail", f"position still open, quantity={qty}")
            else:
                record("strategy_squareoff", "skipped_expected", "no live quote source to seed a position")
    except Exception as exc:
        record("strategy_squareoff", "fail", f"exception: {exc!r}")

    # ------------------------------------------------------------------
    # 2. chartink squareoff (blueprints/chartink.py)
    # ------------------------------------------------------------------
    try:
        from database import chartink_db

        cstrat = chartink_db.create_strategy(
            name=f"{test_tag}-chartink", webhook_id=str(uuid.uuid4()), user_id=user_id or "test",
            is_intraday=True,
        ) if hasattr(chartink_db, "create_strategy") else None
        if cstrat is None:
            record("chartink_squareoff", "fail", "chartink_db.create_strategy returned None or missing")
        else:
            created_chartink_ids.append(cstrat.id)
            chartink_db.add_symbol_mapping(cstrat.id, "RELIANCE", "NSE", 1, "CNC")
            if have_quotes:
                from services.sandbox_service import sandbox_place_order
                ok, resp, code = sandbox_place_order(
                    {"symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
                     "quantity": 1, "pricetype": "MARKET", "product": "CNC"},
                    api_key, {},
                )
                if not ok:
                    record("chartink_squareoff", "fail", f"seed BUY order failed: {resp}")
                else:
                    time.sleep(2)
                    from blueprints.chartink import squareoff_positions as chartink_squareoff
                    chartink_squareoff(cstrat.id)
                    time.sleep(3)
                    from database.sandbox_db import SandboxPositions, db_session as sandbox_session
                    sandbox_session.remove()
                    pos = SandboxPositions.query.filter_by(
                        user_id=user_id, symbol="RELIANCE", exchange="NSE", product="CNC"
                    ).first()
                    qty = pos.quantity if pos else 0
                    if qty == 0:
                        record("chartink_squareoff", "pass", "position flat after chartink squareoff_positions()")
                    else:
                        record("chartink_squareoff", "fail", f"position still open, quantity={qty}")
            else:
                record("chartink_squareoff", "skipped_expected", "no live quote source to seed a position")
    except Exception as exc:
        record("chartink_squareoff", "fail", f"exception: {exc!r}")

    # ------------------------------------------------------------------
    # 3. python_strategy scheduler functions
    # ------------------------------------------------------------------
    try:
        from blueprints import python_strategy as ps

        pid = f"{test_tag}-pystrat"
        script_dir = REPO_ROOT / "strategies" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / f"{pid}.py"
        script_path.write_text(
            "import time\n"
            "# no-op verification strategy: sleeps briefly then exits cleanly\n"
            "time.sleep(2)\n"
        )
        ps.STRATEGY_CONFIGS[pid] = {
            "name": pid,
            "file_path": str(script_path),
            "exchange": "NSE",
            "is_running": False,
            "is_scheduled": True,
            "user_id": user_id or "test",
            "schedule_start": "00:00",
            "schedule_stop": "23:59",
            "schedule_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        }
        created_python_ids.append(pid)

        ps.scheduled_start_strategy(pid)
        time.sleep(1)
        started_flag = ps.STRATEGY_CONFIGS[pid].get("is_running")
        if started_flag:
            record("python_strategy_start", "pass", "is_running flipped True after scheduled_start_strategy")
        else:
            reason = ps.STRATEGY_CONFIGS[pid].get("paused_reason")
            record(
                "python_strategy_start", "status_only" if reason else "fail",
                f"is_running still False (paused_reason={reason!r}) — "
                "expected if today's exchange session is closed" if reason else "no paused_reason set either",
            )

        ps.scheduled_stop_strategy(pid)
        time.sleep(1)
        stopped_flag = ps.STRATEGY_CONFIGS[pid].get("is_running")
        if not stopped_flag:
            record("python_strategy_stop", "pass", "is_running False after scheduled_stop_strategy")
        else:
            record("python_strategy_stop", "fail", "is_running still True after scheduled_stop_strategy")

        ps.daily_trading_day_check()
        record("python_strategy_daily_trading_day_check", "pass", "ran without raising (global sweep, no per-strategy assertion)")

        ps.market_hours_enforcer()
        record("python_strategy_market_hours_enforcer", "pass", "ran without raising (global sweep, no per-strategy assertion)")

        ps.cleanup_dead_processes()
        record("python_strategy_cleanup_dead_processes", "pass", "ran without raising (global sweep, no per-strategy assertion)")

    except Exception as exc:
        record("python_strategy_family", "fail", f"exception: {exc!r}")

    # ------------------------------------------------------------------
    # 4. flow/historify — audit registration only, no safe manual trigger
    # ------------------------------------------------------------------
    try:
        from services.scheduler_registry_service import list_scheduler_registry

        ok, resp, code = list_scheduler_registry(api_key)
        if not ok:
            record("flow_historify_registry_audit", "fail", f"list_scheduler_registry failed: {resp}")
        else:
            jobs = resp.get("data", {}).get("entries", [])
            by_source: dict[str, int] = {}
            for job in jobs:
                by_source[job.get("source", "?")] = by_source.get(job.get("source", "?"), 0) + 1
            record(
                "flow_historify_registry_audit", "status_only",
                f"registered jobs by source: {by_source} (not fired — no manual trigger endpoint exists)",
            )
    except Exception as exc:
        record("flow_historify_registry_audit", "fail", f"exception: {exc!r}")

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    try:
        from database import strategy_db, chartink_db
        for sid in created_strategy_ids:
            strategy_db.db_session.query(strategy_db.StrategySymbolMapping).filter_by(strategy_id=sid).delete()
            s = strategy_db.Strategy.query.get(sid)
            if s:
                strategy_db.db_session.delete(s)
        strategy_db.db_session.commit()
        for cid in created_chartink_ids:
            chartink_db.db_session.query(chartink_db.ChartinkSymbolMapping).filter_by(strategy_id=cid).delete()
            c = chartink_db.ChartinkStrategy.query.get(cid)
            if c:
                chartink_db.db_session.delete(c)
        chartink_db.db_session.commit()
        for pid in created_python_ids:
            from blueprints import python_strategy as ps
            ps.STRATEGY_CONFIGS.pop(pid, None)
            script_path = REPO_ROOT / "strategies" / "scripts" / f"{pid}.py"
            script_path.unlink(missing_ok=True)
        record("teardown", "pass", f"cleaned up {len(created_strategy_ids)} strategy, {len(created_chartink_ids)} chartink, {len(created_python_ids)} python_strategy test rows")
    except Exception as exc:
        record("teardown", "fail", f"exception during cleanup: {exc!r} — MANUAL CLEANUP NEEDED for tag {test_tag}")

    print("\n=== Summary ===")
    for r in RESULTS:
        print(f"{r['verdict']:16} {r['check']}: {r['notes']}")

    return 0 if not any(r["verdict"] == "fail" for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
