"""HF replay-backed master contract builder for stock_simulator broker."""

from __future__ import annotations

import time

import pandas as pd

from database.symbol import SymToken, db_session, init_db
from extensions import socketio
from utils.logging import get_logger

logger = get_logger(__name__)


def _safe_socketio_emit(event: str, payload: dict) -> None:
    try:
        if getattr(socketio, "server", None) is not None:
            socketio.emit(event, payload)
    except Exception as exc:
        logger.debug("socketio emit skipped: %s", exc)


def delete_symtoken_table() -> None:
    logger.info("Deleting symtoken table for stock_simulator rebuild")
    SymToken.query.delete()
    db_session.commit()


def copy_from_dataframe(df: pd.DataFrame, *, broker: str = "stock_simulator") -> None:
    from database.master_contract_status_db import update_status

    records = df.to_dict(orient="records")
    chunk_size = 500
    total = len(records)
    inserted = 0
    for i in range(0, total, chunk_size):
        chunk = records[i : i + chunk_size]
        db_session.bulk_insert_mappings(SymToken, chunk)
        db_session.commit()
        inserted += len(chunk)
        if inserted == total or (i // chunk_size + 1) % 5 == 0:
            pct = int((inserted / total) * 100) if total else 100
            update_status(broker, "downloading", f"Importing simulator symbols: {inserted:,}/{total:,}")
            _safe_socketio_emit(
                "master_contract_download",
                {"status": "downloading", "message": f"Importing {inserted:,}/{total:,}", "progress": 60 + int(pct * 0.4)},
            )


def master_contract_download():
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path
    from database.master_contract_status_db import update_download_stats, update_status

    ensure_trade_integrations_path()
    from trade_integrations.stock_simulator.config import load_sim_config
    from trade_integrations.stock_simulator.master_contract import (
        build_symtoken_rows,
        load_mc_underlyings,
        mc_cache_fingerprint,
    )

    cfg = load_sim_config()
    broker = "stock_simulator"
    started = time.time()
    update_status(broker, "downloading", "Building simulator master contract from HF replay")

    try:
        init_db()
        rows = build_symtoken_rows(data_root=cfg.data_root, replay_date=cfg.replay_date)
        if not rows:
            msg = f"No symtoken rows for replay_date={cfg.replay_date}"
            update_status(broker, "error", msg)
            return _safe_socketio_emit("master_contract_download", {"status": "error", "message": msg})

        delete_symtoken_table()
        df = pd.DataFrame(rows)
        copy_from_dataframe(df, broker=broker)

        fingerprint = mc_cache_fingerprint(replay_date=cfg.replay_date, underlyings=load_mc_underlyings())
        duration = int(time.time() - started)
        exchange_stats = {
            **fingerprint,
            "counts": df.groupby("exchange").size().to_dict(),
        }
        update_download_stats(broker, duration, exchange_stats=exchange_stats)

        msg = f"Built {len(rows)} simulator symbols for {cfg.replay_date}"
        update_status(broker, "success", msg, total_symbols=len(rows))
        return _safe_socketio_emit("master_contract_download", {"status": "success", "message": msg})
    except Exception as exc:
        logger.exception("stock_simulator master contract download failed")
        update_status(broker, "error", str(exc))
        return _safe_socketio_emit("master_contract_download", {"status": "error", "message": str(exc)})


def search_symbols(symbol: str, exchange: str):
    return SymToken.query.filter(
        SymToken.symbol.like(f"%{symbol}%"), SymToken.exchange == exchange
    ).all()
