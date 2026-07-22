"""Replay-backed market data for stock_simulator broker."""

from __future__ import annotations

from typing import Any

from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path
from utils.logging import get_logger

logger = get_logger(__name__)


class BrokerData:
    def __init__(self, auth_token: str, feed_token: str | None = None) -> None:
        self.auth_token = auth_token
        ensure_trade_integrations_path()
        from trade_integrations.stock_simulator.replay import get_replay_service

        self._replay = get_replay_service()

    def get_quotes(self, symbol: str, exchange: str) -> dict[str, Any]:
        return self._replay.get_quote(symbol, exchange)

    def get_multiquotes(self, symbols: list[dict[str, str]]) -> list[dict[str, Any]]:
        return self._replay.get_multiquotes(symbols)

    def get_option_chain(
        self,
        symbol: str,
        exchange: str,
        expiry_date: str | None = None,
        strike_count: int = 10,
    ) -> dict[str, Any]:
        return self._replay.get_option_chain(
            symbol,
            exchange,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )

    def get_history(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        ensure_trade_integrations_path()
        from trade_integrations.stock_simulator.catalog import ReplayCatalog
        from trade_integrations.stock_simulator.config import load_sim_config

        catalog = ReplayCatalog(load_sim_config().data_root)
        rows: list[dict[str, Any]] = []
        for day in catalog.available_dates(symbol, exchange):
            if day < start_date[:10] or day > end_date[:10]:
                continue
            from datetime import datetime
            from zoneinfo import ZoneInfo

            ts = datetime.strptime(f"{day} 15:25", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Kolkata"))
            bar = catalog.bar_at(symbol, exchange, ts)
            if bar:
                rows.append(
                    {
                        "timestamp": day,
                        "open": bar["open"],
                        "high": bar["high"],
                        "low": bar["low"],
                        "close": bar["close"],
                        "volume": bar["volume"],
                    }
                )
        return rows
