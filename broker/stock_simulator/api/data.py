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

    @staticmethod
    def _mode() -> dict[str, Any]:
        from trade_integrations.stock_simulator.mode import effective_mode

        return effective_mode()

    def get_quotes(self, symbol: str, exchange: str) -> dict[str, Any]:
        mode_info = self._mode()
        if mode_info["mode"] == "live":
            try:
                from trade_integrations.stock_simulator.live_quotes import get_live_quote_service

                quote = get_live_quote_service().get_quote(symbol, exchange)
                quote["replay_date"] = None
                return quote
            except Exception:
                logger.warning(
                    "live quote fetch failed for %s/%s; falling back to replay", symbol, exchange,
                    exc_info=True,
                )
                mode_info = {"mode": "replay", "reason": "live_fetch_failed",
                             "replay_date": self._replay.config.replay_date}

        quote = self._replay.get_quote(symbol, exchange)
        quote["mode"] = mode_info["mode"]
        quote["replay_date"] = mode_info.get("replay_date")
        return quote

    def get_multiquotes(self, symbols: list[dict[str, str]]) -> list[dict[str, Any]]:
        mode_info = self._mode()
        if mode_info["mode"] == "live":
            try:
                from trade_integrations.stock_simulator.live_quotes import get_live_quote_service

                results = get_live_quote_service().get_multiquotes(symbols)
                for item in results:
                    if "data" in item:
                        item["data"]["replay_date"] = None
                return results
            except Exception:
                logger.warning("live multiquote fetch failed; falling back to replay", exc_info=True)
                mode_info = {"mode": "replay", "reason": "live_fetch_failed",
                             "replay_date": self._replay.config.replay_date}

        results = self._replay.get_multiquotes(symbols)
        for item in results:
            if "data" in item:
                item["data"]["mode"] = mode_info["mode"]
                item["data"]["replay_date"] = mode_info.get("replay_date")
        return results

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

        if self._mode()["mode"] == "live":
            try:
                from trade_integrations.stock_simulator.live_quotes import get_live_quote_service

                return get_live_quote_service().get_option_chain(
                    symbol,
                    spot_exchange,
                    expiry_date=expiry_date,
                    strike_count=strike_count,
                )
            except Exception:
                logger.warning(
                    "live option chain fetch failed for %s/%s; falling back to replay",
                    symbol, exchange, exc_info=True,
                )

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
            "simulated": quote.get("mode") != "live",
            "source": quote.get("source", "stock_simulator"),
            "mode": quote.get("mode"),
            "replay_date": quote.get("replay_date"),
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
        from trade_integrations.stock_simulator.master_contract import parse_openalgo_option_symbol

        # Live history only covers the underlying's own cash/index series —
        # option-chain candles aren't exposed by INDmoney's historical
        # endpoint, so options always fall through to the replay catalog
        # even when the broker is otherwise in live mode.
        is_option = exchange.upper() in {"NFO", "BFO"} or parse_openalgo_option_symbol(symbol)
        if not is_option and self._mode()["mode"] == "live":
            try:
                from trade_integrations.stock_simulator.live_quotes import get_live_quote_service

                rows = get_live_quote_service().get_history(
                    symbol, exchange, interval, start_date, end_date
                )
                if rows:
                    df = pd.DataFrame(rows)
                    if "oi" not in df.columns:
                        df["oi"] = 0
                    return df
            except Exception:
                logger.warning(
                    "live history fetch failed for %s/%s; falling back to replay",
                    symbol, exchange, exc_info=True,
                )

        return self._replay_history(symbol, exchange, interval, start_date, end_date, is_option=is_option)

    def _replay_history(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: str,
        end_date: str,
        *,
        is_option: bool,
    ) -> pd.DataFrame:
        from trade_integrations.stock_simulator.catalog import ReplayCatalog
        from trade_integrations.stock_simulator.config import load_sim_config

        interval = (interval or "D").strip()
        catalog = ReplayCatalog(load_sim_config().data_root)
        start = str(start_date)[:10]
        end = str(end_date)[:10]

        # The chart client only knows the real wall-clock date and always
        # requests a range anchored on it (e.g. "last 30 days from today"),
        # not the day actually being replayed. The replay catalog's data
        # root is the same directory the live recorder writes into, so a
        # real "today" query can come back non-empty with genuine
        # live-recorded candles even while a different day is armed for
        # replay — silently showing the chart real-world dates instead of
        # the simulator's simulated day. Whenever this broker is actually
        # in replay mode, the active replay window is authoritative: source
        # the query range from it instead of trusting the caller's dates,
        # so real-today data in the same catalog can never masquerade as
        # the requested replay window.
        if is_option or self._mode()["mode"] != "live":
            fallback_start, fallback_end = self._active_replay_window()
            if fallback_start:
                start, end = fallback_start, fallback_end

        if is_option:
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

    def _active_replay_window(self) -> tuple[str | None, str | None]:
        """The replay day(s) actually loaded — the whole rotation week when
        week-mode is on, else just the single active replay date."""
        config = self._replay.config
        if config.week_mode and config.week_dates:
            dates = sorted(config.week_dates)
            return dates[0], dates[-1]
        if config.replay_date:
            return config.replay_date, config.replay_date
        return None, None

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
        # Clip to the sim clock so a replay day's already-recorded but
        # not-yet-reached minutes (the catalog may hold a full day even
        # though sim_now is only partway through it) never render as
        # "future" candles ahead of where the simulator actually is.
        sim_now = self._replay.sim_now()
        day_frame = day_frame[day_frame["ts_ist"] <= sim_now]
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


# hotreload watchdog test Wed Aug 19 15:05:39 IST 2026
