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

        interval = (interval or "D").strip()
        catalog = ReplayCatalog(load_sim_config().data_root)
        start = start_date[:10]
        end = end_date[:10]

        if interval in {"1m", "1minute"}:
            rows = self._history_intraday(catalog, symbol, exchange, start, end, bar_minutes=1)
        elif interval in {"5m", "5minute"}:
            rows = self._history_intraday(catalog, symbol, exchange, start, end, bar_minutes=5)
        else:
            rows = []
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
                        }
                    )

        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
        df = pd.DataFrame(rows)
        if "oi" not in df.columns:
            df["oi"] = 0
        return df

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
        day_frame = frame[(frame["day"] >= start) & (frame["day"] <= end)]
        if day_frame.empty:
            return []
        if bar_minutes > 1:
            bucket = day_frame["ts_ist"].dt.floor(f"{bar_minutes}min")
            day_frame = day_frame.assign(bucket=bucket).drop_duplicates(subset=["day", "bucket"], keep="last")
        rows: list[dict[str, Any]] = []
        for _, hit in day_frame.iterrows():
            vol = hit.get("volume")
            rows.append(
                {
                    "timestamp": hit["ts_ist"].isoformat(),
                    "open": float(hit["open"]),
                    "high": float(hit["high"]),
                    "low": float(hit["low"]),
                    "close": float(hit["close"]),
                    "volume": int(vol) if vol is not None and pd.notna(vol) else 0,
                }
            )
        return rows
