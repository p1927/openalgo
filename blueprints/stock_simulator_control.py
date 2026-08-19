"""Server-to-server control endpoint for the NSE stock simulator's replay clock.

Lets an external caller (VibeTrading's recording UI) arm a previously
recorded trading day for replay without restarting OpenAlgo — the simulator's
`ReplayService` reconstructs itself from env vars on every
`get_replay_service(reload=True)` call (see
`integrations/trade_integrations/stock_simulator/replay.py`), so setting the
env vars and reloading is enough.

Not a browser-facing page and not `/api/v1/` broker-key auth: this is called
server-to-server from VibeTrading's backend, which holds neither a browser
session nor a broker API key. Gated by a shared-secret token
(`SIMULATOR_CONTROL_TOKEN`) instead — fails closed if that token isn't
configured, rather than running this control surface open.
"""

import hmac
import os
from datetime import date

from flask import Blueprint, jsonify, request

from limiter import limiter
from utils.logging import get_logger

logger = get_logger(__name__)

stock_simulator_control_bp = Blueprint(
    "stock_simulator_control_bp", __name__, url_prefix="/stock_simulator/control"
)

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "50 per second")

_TOKEN_HEADER = "X-Simulator-Control-Token"
_SIM_DB_ENV = (
    ("sim_replay_date", "NSE_REPLAY_DATE"),
    ("sim_replay_end_date", "NSE_REPLAY_END_DATE"),
    ("sim_replay_speed", "NSE_REPLAY_SPEED"),
    ("sim_replay_loop", "NSE_REPLAY_LOOP"),
)


def _require_control_token():
    """Return an error response dict if the request is not authorized, else None."""
    configured = (os.getenv("SIMULATOR_CONTROL_TOKEN") or "").strip()
    if not configured:
        return jsonify(
            {"status": "error", "message": "simulator control endpoint not configured"}
        ), 503
    provided = (request.headers.get(_TOKEN_HEADER) or "").strip()
    if not provided or not hmac.compare_digest(provided, configured):
        return jsonify({"status": "error", "message": "invalid control token"}), 401
    return None


def _get_replay_service(*, reload: bool = False):
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.stock_simulator.replay import get_replay_service

    return get_replay_service(reload=reload)


@stock_simulator_control_bp.errorhandler(429)
def ratelimit_handler(e):
    return jsonify(
        {"status": "error", "message": "Rate limit exceeded. Please try again later."}
    ), 429


@stock_simulator_control_bp.route("/replay/start", methods=["POST"])
@limiter.limit(API_RATE_LIMIT)
def start_replay():
    """Arm the simulator to replay a specific previously recorded day."""
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized

    body = request.get_json(silent=True) or {}
    replay_date = str(body.get("date") or "").strip()
    if not replay_date:
        return jsonify({"status": "error", "message": "date is required"}), 400
    try:
        date.fromisoformat(replay_date[:10])
    except ValueError:
        return jsonify(
            {"status": "error", "message": "date must be YYYY-MM-DD"}
        ), 400

    end_date = str(body.get("end_date") or "").strip()
    if end_date:
        try:
            date.fromisoformat(end_date[:10])
        except ValueError:
            return jsonify(
                {"status": "error", "message": "end_date must be YYYY-MM-DD"}
            ), 400

    speed = body.get("speed")
    loop = body.get("loop")

    try:
        from database.sandbox_db import set_config

        set_config("sim_replay_date", replay_date)
        set_config("sim_replay_end_date", end_date)
        if speed is not None:
            set_config("sim_replay_speed", speed)
        if loop is not None:
            set_config("sim_replay_loop", "1" if loop else "0")
    except Exception:
        logger.exception("failed to persist simulator replay settings to sandbox_db")

    # Capture the prior replay date BEFORE we mutate os.environ so the
    # MC-rebuild trigger below can decide whether the user actually
    # armed a new day. The default ``"2021-03-25"`` is a sentinel shared
    # with ``sandbox.api_simulator_config`` and ``utils.auth_utils`` —
    # on a truly first arm (env unset) the sentinel forces the
    # ``new != prior`` check to fire, which kicks off the MC rebuild.
    # Without the sentinel the first arm would skip the rebuild and
    # leave the user staring at a dropdown whose expiries the chain
    # call can't honour (the symtoken table would be empty).
    prior_replay_date = os.getenv("NSE_REPLAY_DATE", "2021-03-25").strip()[:10]

    os.environ["NSE_REPLAY_DATE"] = replay_date
    if end_date:
        os.environ["NSE_REPLAY_END_DATE"] = end_date
    else:
        # No range this time — drop any end date left over from a previous
        # range-arm so a plain single-day arm doesn't inherit it.
        os.environ.pop("NSE_REPLAY_END_DATE", None)
    if speed is not None:
        os.environ["NSE_REPLAY_SPEED"] = str(speed)
    if loop is not None:
        os.environ["NSE_REPLAY_LOOP"] = "1" if loop else "0"
    os.environ["STOCK_SIMULATOR_MODE"] = "replay"
    os.environ.setdefault("HUB_NO_LEARN", "1")

    try:
        service = _get_replay_service(reload=True)
    except Exception as exc:
        logger.exception("failed to reload replay service")
        return jsonify({"status": "error", "message": str(exc)}), 500

    # --- BEGIN MC auto-rebuild (mirror sandbox.api_simulator_config) -----
    # When the user arms a different replay day, the symtoken table
    # (built by ``master_contract_download``) is now anchored to the
    # old day. Without a rebuild, the option-chain endpoint would
    # 404 on any (strike, expiry) the new day introduced. The Sandbox
    # UI's arm-replay path already does this; the server-to-server
    # control endpoint (used by the Vibe agent) didn't, which is a
    # real bug now that the expiry dropdown surfaces bundle-driven
    # expiries the user can actually pick.
    #
    # Match sandbox's pattern: only kick off a rebuild when the
    # replay date actually changed. ``async_master_contract_download``
    # runs in a daemon thread so the HTTP response is not blocked.
    # ----------------------------------------------------------------------
    mc_refresh = None
    new_replay_date = os.getenv("NSE_REPLAY_DATE", "").strip()[:10]
    if prior_replay_date and new_replay_date and new_replay_date != prior_replay_date:
        from threading import Thread

        from utils.auth_utils import async_master_contract_download

        rebuild_thread = Thread(
            target=async_master_contract_download,
            args=("stock_simulator",),
            daemon=True,
        )
        rebuild_thread.start()
        # Bounded wait so the response the frontend acts on (e.g. immediately
        # re-fetching /search/api/expiries to populate the dropdown) doesn't
        # race the rebuild. The rebuild is normally sub-second (delete +
        # reinsert symtoken rows for one day); if it somehow runs long we
        # still return rather than block the request indefinitely — the
        # dropdown may just need a manual refresh in that rare case.
        rebuild_thread.join(timeout=8.0)
        mc_refresh = "completed" if not rebuild_thread.is_alive() else "started"
    # --- END MC auto-rebuild --------------------------------------------

    payload = {"status": "ok", **service.status()}
    if mc_refresh:
        payload["master_contract_refresh"] = mc_refresh
    return jsonify(payload)


@stock_simulator_control_bp.route("/replay/pause", methods=["POST"])
@limiter.limit(API_RATE_LIMIT)
def pause_replay():
    """Freeze the simulator's replay clock without unloading it."""
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized

    try:
        service = _get_replay_service()
    except Exception as exc:
        logger.exception("failed to load replay service for pause")
        return jsonify({"status": "error", "message": str(exc)}), 500

    service.pause()
    return jsonify({"status": "ok", **service.status()})


@stock_simulator_control_bp.route("/replay/resume", methods=["POST"])
@limiter.limit(API_RATE_LIMIT)
def resume_replay():
    """Resume a previously paused simulator clock."""
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized

    try:
        service = _get_replay_service()
    except Exception as exc:
        logger.exception("failed to load replay service for resume")
        return jsonify({"status": "error", "message": str(exc)}), 500

    service.resume()
    return jsonify({"status": "ok", **service.status()})


@stock_simulator_control_bp.route("/replay/seek", methods=["POST"])
@limiter.limit(API_RATE_LIMIT)
def seek_replay():
    """Scrub the simulator's replay clock to an arbitrary point in the armed day."""
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized

    body = request.get_json(silent=True) or {}
    time_str = str(body.get("time") or "").strip()
    if not time_str:
        return jsonify({"status": "error", "message": "time is required"}), 400

    try:
        service = _get_replay_service()
    except Exception as exc:
        logger.exception("failed to load replay service for seek")
        return jsonify({"status": "error", "message": str(exc)}), 500

    from datetime import datetime
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    replay_date = service.clock.replay_date
    try:
        if len(time_str) <= 8 and ":" in time_str and "T" not in time_str:
            # HH:MM or HH:MM:SS on the currently armed replay date.
            parts = time_str.split(":")
            hour, minute = int(parts[0]), int(parts[1])
            second = int(parts[2]) if len(parts) > 2 else 0
            target = datetime.strptime(replay_date, "%Y-%m-%d").replace(
                hour=hour, minute=minute, second=second, microsecond=0, tzinfo=ist
            )
        else:
            target = datetime.fromisoformat(time_str)
            if target.tzinfo is None:
                target = target.replace(tzinfo=ist)
    except (ValueError, IndexError):
        return jsonify(
            {"status": "error", "message": "time must be HH:MM[:SS] or an ISO datetime"}
        ), 400

    service.seek(target)
    return jsonify({"status": "ok", **service.status()})


@stock_simulator_control_bp.route("/replay/stop", methods=["POST"])
@limiter.limit(API_RATE_LIMIT)
def stop_replay():
    """Tear the simulator down so OpenAlgo falls back to its real broker."""
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized

    try:
        service = _get_replay_service()
    except Exception as exc:
        logger.exception("failed to load replay service for stop")
        return jsonify({"status": "error", "message": str(exc)}), 500

    service.stop_simulator()

    # Clear the persisted sandbox_db rows too, not just the in-process env
    # var — otherwise hydrate_simulator_env_from_db() re-arms replay mode
    # from the stale sim_replay_* config on the next OpenAlgo restart.
    try:
        from database.sandbox_db import set_config

        for db_key, _env_key in _SIM_DB_ENV:
            set_config(db_key, "")
    except Exception:
        logger.exception("failed to clear persisted simulator replay settings in sandbox_db")

    return jsonify(
        {
            "status": "ok",
            "message": "simulator stopped; OpenAlgo will fall back to its real broker",
            **service.status(),
        }
    )


@stock_simulator_control_bp.route("/replay/calendar", methods=["GET"])
@limiter.limit(API_RATE_LIMIT)
def replay_calendar():
    """Per-day availability + bar counts for the calendar heatmap.

    Returns one row per union-of-underlyings day, with coverage flags and
    bar counts so the UI can color squares by which underlyings are usable.
    """
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized

    try:
        from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

        ensure_trade_integrations_path()
        from trade_integrations.stock_simulator.catalog import ReplayCatalog
        from trade_integrations.stock_simulator.config import load_sim_config
    except Exception as exc:
        logger.exception("failed to load simulator modules for calendar")
        return jsonify({"status": "error", "message": str(exc)}), 500

    data_root = load_sim_config().data_root
    catalog = ReplayCatalog(data_root)
    underlyings = (
        ("NIFTY", "NSE_INDEX"),
        ("BANKNIFTY", "NSE_INDEX"),
        ("SENSEX", "BSE_INDEX"),
    )
    by_day: dict[str, dict[str, object]] = {}
    for symbol, exchange in underlyings:
        counts = catalog.day_counts(symbol, exchange)
        for day, count in counts.items():
            row = by_day.setdefault(
                day,
                {
                    "date": day,
                    "has_nifty": False,
                    "has_banknifty": False,
                    "has_sensex": False,
                    "nifty_rows": 0,
                    "banknifty_rows": 0,
                    "sensex_rows": 0,
                },
            )
            if symbol == "NIFTY":
                row["has_nifty"] = True
                row["nifty_rows"] = count
            elif symbol == "BANKNIFTY":
                row["has_banknifty"] = True
                row["banknifty_rows"] = count
            elif symbol == "SENSEX":
                row["has_sensex"] = True
                row["sensex_rows"] = count

    rows = sorted(by_day.values(), key=lambda r: r["date"], reverse=True)  # type: ignore[arg-type,return-value]
    return jsonify({"status": "ok", "days": rows, "underlyings": [u[0] for u in underlyings]})


@stock_simulator_control_bp.route("/replay/status", methods=["GET"])
@limiter.limit(API_RATE_LIMIT)
def replay_status():
    """Current simulator replay clock state, without forcing a reload."""
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized

    try:
        service = _get_replay_service()
    except Exception as exc:
        logger.exception("failed to read replay service status")
        return jsonify({"status": "error", "message": str(exc)}), 500

    return jsonify({"status": "ok", **service.status()})
