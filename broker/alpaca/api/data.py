"""Alpaca market data — quotes, history, multiquotes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from broker.alpaca.api._http import alpaca_request
from broker.alpaca.api.baseurl import data_base, data_feed
from broker.alpaca.mapping.order_data import normalize_us_symbol
from utils.logging import get_logger

logger = get_logger(__name__)

_TIMEFRAME_MAP = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "30m": "30Min",
    "1h": "1Hour",
    "60m": "1Hour",
    "D": "1Day",
    "1d": "1Day",
    "1day": "1Day",
}


def _f(value, default=0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _i(value, default=0) -> int:
    try:
        return int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        return default


class BrokerData:
    def __init__(self, auth_token: str, feed_token: str | None = None) -> None:
        self.auth_token = auth_token

    def get_quotes(self, symbol: str, exchange: str) -> dict[str, Any]:
        clean = normalize_us_symbol(symbol)
        feed = data_feed()
        url = f"{data_base()}/v2/stocks/{clean}/snapshot"
        status, payload = alpaca_request(
            "GET", url, self.auth_token, params={"feed": feed}
        )
        if status >= 400 or not isinstance(payload, dict):
            raise Exception(f"Alpaca snapshot failed ({status}): {payload}")

        quote = payload.get("latestQuote") or {}
        trade = payload.get("latestTrade") or {}
        daily = payload.get("dailyBar") or payload.get("minuteBar") or {}

        bid = quote.get("bp")
        ask = quote.get("ap")
        ltp = trade.get("p")
        if ltp is None and bid is not None and ask is not None:
            ltp = (_f(bid) + _f(ask)) / 2.0
        elif ltp is None:
            ltp = bid if bid is not None else ask

        return {
            "ltp": _f(ltp),
            "bid": _f(bid),
            "ask": _f(ask),
            "open": _f(daily.get("o")),
            "high": _f(daily.get("h")),
            "low": _f(daily.get("l")),
            "close": _f(daily.get("c")),
            "volume": _i(daily.get("v")),
            "exchange": exchange.upper(),
            "symbol": clean,
        }

    def get_multiquotes(self, symbols: list[dict[str, str]]) -> list[dict[str, Any]]:
        results = []
        for item in symbols:
            symbol = item.get("symbol", "")
            exchange = item.get("exchange", "NASDAQ")
            try:
                quote = self.get_quotes(symbol, exchange)
                results.append({"symbol": symbol, "exchange": exchange, "data": quote})
            except Exception as exc:
                logger.exception("Alpaca multiquote failed for %s", symbol)
                results.append({"symbol": symbol, "exchange": exchange, "error": str(exc)})
        return results

    def get_history(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        clean = normalize_us_symbol(symbol)
        timeframe = _TIMEFRAME_MAP.get(interval, "1Day")
        feed = data_feed()
        url = f"{data_base()}/v2/stocks/{clean}/bars"
        params = {
            "timeframe": timeframe,
            "start": f"{start_date[:10]}T00:00:00Z",
            "end": f"{end_date[:10]}T23:59:59Z",
            "limit": 10000,
            "feed": feed,
            "adjustment": "split",
        }
        status, payload = alpaca_request("GET", url, self.auth_token, params=params)
        if status >= 400:
            raise Exception(f"Alpaca history failed ({status}): {payload}")

        bars = payload.get("bars") if isinstance(payload, dict) else []
        rows = []
        for bar in bars or []:
            ts = bar.get("t")
            rows.append(
                {
                    "timestamp": ts,
                    "open": _f(bar.get("o")),
                    "high": _f(bar.get("h")),
                    "low": _f(bar.get("l")),
                    "close": _f(bar.get("c")),
                    "volume": _i(bar.get("v")),
                }
            )
        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        frame = pd.DataFrame(rows)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
        return frame.dropna(subset=["timestamp"])

    def get_intervals(self) -> list[str]:
        return list(_TIMEFRAME_MAP.keys())
