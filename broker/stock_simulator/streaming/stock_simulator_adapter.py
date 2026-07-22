"""Replay-driven WebSocket adapter for stock_simulator broker."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path
from websocket_proxy.base_adapter import BaseBrokerWebSocketAdapter

logger = logging.getLogger("stock_simulator_websocket")

_MODE_LABEL = {1: "LTP", 2: "QUOTE", 3: "DEPTH", 4: "DEPTH", 5: "DEPTH"}


def _sim_timestamp_ms(sim_ts: str | None) -> int:
    if not sim_ts:
        return int(time.time() * 1000)
    try:
        parsed = datetime.fromisoformat(str(sim_ts))
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError):
        return int(time.time() * 1000)


class Stock_simulatorWebSocketAdapter(BaseBrokerWebSocketAdapter):
    """Push replay quotes over the OpenAlgo WebSocket proxy (REST-backed ticks)."""

    def __init__(self) -> None:
        super().__init__()
        self.broker_name = "stock_simulator"
        self.user_id: str | None = None
        self.running = False
        self._stream_thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self._last_ltp: dict[str, float] = {}
        self._last_sim_ts: dict[str, str | None] = {}

    def initialize(self, broker_name: str, user_id: str, auth_data: dict | None = None) -> None:
        self.user_id = user_id
        self.broker_name = broker_name
        ensure_trade_integrations_path()
        logger.info("Initialized replay WebSocket for stock_simulator, user: %s", user_id)

    def connect(self) -> dict[str, Any]:
        with self.lock:
            if self.running:
                return {"success": True, "message": "Already connected"}
            self.running = True
            self.connected = True
            self._stream_thread = threading.Thread(
                target=self._replay_stream_loop,
                daemon=True,
                name="StockSimulatorReplayWS",
            )
            self._stream_thread.start()
            logger.info("stock_simulator replay WebSocket streaming started")
            return {"success": True}

    def disconnect(self) -> None:
        with self.lock:
            self.running = False
            self.connected = False
            self._stream_thread = None
            logger.info("stock_simulator replay WebSocket disconnected")

    def subscribe(
        self,
        symbol: str,
        exchange: str,
        mode: int = 2,
        depth_level: int = 5,
    ) -> dict[str, Any]:
        with self.lock:
            sub_key = f"{exchange}_{symbol}"
            self.subscriptions[sub_key] = {
                "symbol": symbol,
                "exchange": exchange,
                "mode": mode,
                "depth_level": depth_level,
            }
            logger.info("Replay subscribe %s (%s mode=%s)", symbol, exchange, mode)
            return {
                "status": "success",
                "message": f"Subscribed to {symbol}",
                "broker": self.broker_name,
                "exchange": exchange,
                "supported_depth": 5,
                "fallback_depth": 5,
            }

    def unsubscribe(self, symbol: str, exchange: str, mode: int | None = None) -> dict[str, Any]:
        with self.lock:
            sub_key = f"{exchange}_{symbol}"
            self.subscriptions.pop(sub_key, None)
            self._last_ltp.pop(sub_key, None)
            self._last_sim_ts.pop(sub_key, None)
            return {"status": "success", "message": f"Unsubscribed from {symbol}"}

    def _replay_stream_loop(self) -> None:
        ensure_trade_integrations_path()
        from trade_integrations.stock_simulator.replay import get_replay_service

        while self.running:
            try:
                with self.lock:
                    subs = list(self.subscriptions.values())
                if not subs:
                    time.sleep(0.25)
                    continue

                svc = get_replay_service()
                for sub in subs:
                    symbol = sub["symbol"]
                    exchange = sub["exchange"]
                    mode = int(sub.get("mode") or 2)
                    sub_key = f"{exchange}_{symbol}"
                    try:
                        quote = svc.get_quote(symbol, exchange)
                    except Exception as exc:
                        logger.debug("replay quote miss %s/%s: %s", symbol, exchange, exc)
                        continue

                    ltp = float(quote.get("ltp") or 0)
                    if ltp <= 0:
                        continue
                    sim_ts_str = quote.get("sim_ts")
                    prev_ltp = self._last_ltp.get(sub_key)
                    prev_sim = self._last_sim_ts.get(sub_key)
                    if (
                        prev_ltp is not None
                        and prev_sim == sim_ts_str
                        and abs(prev_ltp - ltp) < 0.001
                    ):
                        continue
                    self._last_ltp[sub_key] = ltp
                    self._last_sim_ts[sub_key] = sim_ts_str

                    tick_ms = _sim_timestamp_ms(sim_ts_str)
                    market_data: dict[str, Any] = {
                        "symbol": symbol,
                        "exchange": exchange,
                        "mode": mode,
                        "timestamp": tick_ms,
                        "ltp": ltp,
                        "ltt": tick_ms,
                        "simulated": True,
                        "sim_ts": quote.get("sim_ts"),
                    }
                    if mode >= 2:
                        market_data.update(
                            {
                                "volume": int(quote.get("volume") or 0),
                                "oi": int(quote.get("oi") or 0),
                                "open": float(quote.get("open") or ltp),
                                "high": float(quote.get("high") or ltp),
                                "low": float(quote.get("low") or ltp),
                                "close": float(quote.get("close") or ltp),
                                "bid": float(quote.get("bid") or ltp),
                                "ask": float(quote.get("ask") or ltp),
                            }
                        )
                    mode_str = _MODE_LABEL.get(mode, "QUOTE")
                    topic = f"{exchange}_{symbol}_{mode_str}"
                    self.publish_market_data(topic, market_data)
            except Exception:
                logger.exception("stock_simulator replay stream tick failed")
            time.sleep(1.0)
