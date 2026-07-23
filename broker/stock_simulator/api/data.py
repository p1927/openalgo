"""Replay-backed market data for stock_simulator broker."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path
from utils.logging import get_logger

logger = get_logger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_INTRADAY_MINUTES: dict[str, int] = {
    "1m": 1,
    "1minute": 1,
    "5m": 5,
    "5minute": 5,
    "15m": 15,
    "15minute": 15,
    "30m": 30,
    "30minute": 30,
    "1h": 60,
    "60m": 60,
    "60minute": 60,
}


class BrokerData:
    timeframe_map = {
        "1m": "1minute",
        "5m": "5minute",
        "15m": "15minute",
        "30m": "30minute",
        "1h": "60minute",
        "D": "1day",
    }

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
        *,
        underlying_exchange: str | None = None,
    ) -> dict[str, Any]:
        spot_exchange = underlying_exchange or exchange
        if exchange in {"NFO", "BFO"} and spot_exchange == exchange:
            spot_exchange = "NSE_INDEX" if exchange == "NFO" else "BSE_INDEX"
        return self._replay.get_option_chain(
            symbol,
            spot_exchange,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )

    def get_depth(self, symbol: str, exchange: str) -> dict[str, Any]:
        quote = self.get_quotes(symbol, exchange)
        ltp = float(quote.get("ltp") or 0)
        spread = max(0.05, ltp * 0.0001)
        return {
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
            "ltp": ltp,
            "simulated": True,
            "source": "stock_simulator",
            "bids": [{"price": round(ltp - spread, 2), "quantity": 100}],
            "asks": [{"price": round(ltp + spread, 2), "quantity": 100}],
        }

    def get_intervals(self) -> list[str]:
        return list(self.timeframe_map.keys())

    def get_history(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        ensure_trade_integrations_path()
        from trade_integrations.stock_simulator.catalog import ReplayCatalog
        from trade_integrations.stock_simulator.config import load_sim_config
        from trade_integrations.stock_simulator.master_contract import parse_openalgo_option_symbol

        interval = (interval or "D").strip()
        catalog = ReplayCatalog(load_sim_config().data_root)
        start = start_date[:10]
        end = end_date[:10]

        if exchange.upper() in {"NFO", "BFO"} or parse_openalgo_option_symbol(symbol):
            rows = self._history_options(symbol, exchange, start, end, interval)
        elif interval in _INTRADAY_MINUTES:
            rows = self._history_intraday(
                catalog, symbol, exchange, start, end, bar_minutes=_INTRADAY_MINUTES[interval]
            )
        elif interval in {"D", "1d", "1day"}:
            rows = self._history_daily(catalog, symbol, exchange, start, end)
        else:
            rows = []

        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
        df = pd.DataFrame(rows)
        if "oi" not in df.columns:
            df["oi"] = 0
        return df

    def _history_options(
        self,
        symbol: str,
        exchange: str,
        start: str,
        end: str,
        interval: str,
    ) -> list[dict[str, Any]]:
        from trade_integrations.stock_simulator.config import load_sim_config
        from trade_integrations.stock_simulator.options.replay_store import OptionsReplayStore

        if interval in _INTRADAY_MINUTES:
            store = OptionsReplayStore(load_sim_config().data_root)
            return store.history_bars(
                symbol, exchange, start, end, bar_minutes=_INTRADAY_MINUTES[interval]
            )

        if interval not in {"D", "1d", "1day"}:
            return []

        store = OptionsReplayStore(load_sim_config().data_root)
        return store.history_bars(symbol, exchange, start, end, bar_minutes=1440)

    def _history_daily(
        self,
        catalog: Any,
        symbol: str,
        exchange: str,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for day in catalog.available_dates(symbol, exchange):
            if day < start or day > end:
                continue
            ts = datetime.strptime(f"{day} 15:25", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
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
                        "oi": 0,
                    }
                )
        return rows

    def _history_intraday(
        self,
        catalog: Any,
        symbol: str,
        exchange: str,
        start: str,
        end: str,
        *,
        bar_minutes: int,
    ) -> list[dict[str, Any]]:
        from trade_integrations.stock_simulator.hf_paths import index_slug

        slug = index_slug(symbol, exchange)
        if not slug:
            return []
        frame = catalog._load_symbol(symbol, exchange)
        if frame.empty:
            return []
        day_frame = frame[(frame["day"] >= start) & (frame["day"] <= end)].copy()
        if day_frame.empty:
            return []

        if bar_minutes > 1:
            bucket = day_frame["ts_ist"].dt.floor(f"{bar_minutes}min")
            day_frame = (
                day_frame.assign(bucket=bucket)
                .groupby(["day", "bucket"], as_index=False)
                .agg(
                    open=("open", "first"),
                    high=("high", "max"),
                    low=("low", "min"),
                    close=("close", "last"),
                    volume=("volume", "sum"),
                    ts_ist=("ts_ist", "last"),
                )
            )

        rows: list[dict[str, Any]] = []
        for _, hit in day_frame.sort_values("ts_ist").iterrows():
            vol = hit.get("volume")
            rows.append(
                {
                    "timestamp": hit["ts_ist"].isoformat(),
                    "open": float(hit["open"]),
                    "high": float(hit["high"]),
                    "low": float(hit["low"]),
                    "close": float(hit["close"]),
                    "volume": int(vol) if vol is not None and pd.notna(vol) else 0,
                    "oi": 0,
                }
            )
        return rows
