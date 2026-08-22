"""Replay-driven WebSocket adapter for stock_simulator broker."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any

from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path
from websocket_proxy.base_adapter import BaseBrokerWebSocketAdapter

logger = logging.getLogger("stock_simulator_websocket")

_MODE_LABEL = {1: "LTP", 2: "QUOTE", 3: "DEPTH", 4: "DEPTH", 5: "DEPTH"}
_RECV_POLL_TIMEOUT_S = 1.0
_MAX_BACKOFF_S = 10.0


def _sim_timestamp_ms(sim_ts: str | None) -> int:
    if not sim_ts:
        return int(time.time() * 1000)
    try:
        parsed = datetime.fromisoformat(str(sim_ts))
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError):
        return int(time.time() * 1000)


class Stock_simulatorWebSocketAdapter(BaseBrokerWebSocketAdapter):
    """Push replay quotes over the OpenAlgo WebSocket proxy.

    Consumes the shared `stock_simulator` service's own `/stream` WS route
    (one persistent connection, server pushes ticks on its own cadence)
    instead of polling REST `/data/quote` once per subscribed symbol per
    tick — see
    .claude/backlog/items/2026-08-21-stock-simulator-ws-adapter-subscribe-stream.md.

    Uses `websockets.sync.client`, which is built on plain blocking
    `socket`/`ssl` calls rather than `asyncio`, so it stays safe under
    gunicorn+eventlet (eventlet monkey-patches `socket` but forbids
    `asyncio` — see openalgo/CLAUDE.md's "No asyncio... under
    eventlet+gunicorn" invariant and `telegram_bot_service.py`'s
    `_render_plotly_png` docstring for why an asyncio-based client would be
    a landmine here).
    """

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
                target=self._stream_loop,
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

    def _desired_subscriptions(self) -> set[tuple[str, str]]:
        with self.lock:
            return {(sub["symbol"], sub["exchange"]) for sub in self.subscriptions.values()}

    def _mode_for(self, symbol: str, exchange: str) -> int:
        with self.lock:
            sub = self.subscriptions.get(f"{exchange}_{symbol}")
            return int(sub.get("mode") or 2) if sub else 2

    def _stream_loop(self) -> None:
        ensure_trade_integrations_path()
        import websockets.sync.client as ws_client
        from websockets.exceptions import WebSocketException

        from trade_integrations.stock_simulator.client import StockSimulatorClient

        client = StockSimulatorClient()
        backoff = 1.0

        while self.running:
            if not client.is_configured:
                # No control token configured — same fail-closed posture as
                # StockSimulatorClient.is_configured. Wait rather than spin.
                time.sleep(1.0)
                continue
            try:
                with ws_client.connect(client.stream_url, open_timeout=10) as ws:
                    logger.info("stock_simulator stream connected")
                    backoff = 1.0
                    sent_subs: set[tuple[str, str]] = set()
                    while self.running:
                        desired = self._desired_subscriptions()
                        for sym, exch in desired - sent_subs:
                            ws.send(json.dumps({"action": "subscribe", "symbol": sym, "exchange": exch}))
                        for sym, exch in sent_subs - desired:
                            ws.send(json.dumps({"action": "unsubscribe", "symbol": sym, "exchange": exch}))
                        sent_subs = desired

                        try:
                            raw = ws.recv(timeout=_RECV_POLL_TIMEOUT_S)
                        except TimeoutError:
                            continue
                        try:
                            payload = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        self._handle_snapshot(payload)
            except (WebSocketException, OSError, TimeoutError) as exc:
                logger.warning("stock_simulator stream disconnected: %s", exc)
            except Exception:
                logger.exception("stock_simulator stream loop failed")
            if not self.running:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_S)

    def _handle_snapshot(self, payload: dict[str, Any]) -> None:
        mode_info = payload.get("mode") or {}
        is_live = mode_info.get("mode") == "live"
        for entry in payload.get("quotes") or []:
            symbol = entry.get("symbol")
            exchange = entry.get("exchange")
            quote = entry.get("data")
            if not symbol or not exchange or not quote:
                continue
            self._publish_quote(symbol, exchange, quote, is_live=is_live)

    def _publish_quote(self, symbol: str, exchange: str, quote: dict[str, Any], *, is_live: bool) -> None:
        sub_key = f"{exchange}_{symbol}"
        mode = self._mode_for(symbol, exchange)

        ltp = float(quote.get("ltp") or 0)
        if ltp <= 0:
            return
        sim_ts_str = quote.get("sim_ts")
        prev_ltp = self._last_ltp.get(sub_key)
        prev_sim = self._last_sim_ts.get(sub_key)
        if (
            prev_ltp is not None
            and prev_sim == sim_ts_str
            and abs(prev_ltp - ltp) < 0.001
        ):
            return
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
            "simulated": not is_live,
            "sim_source": "live" if is_live else "replay",
            "sim_ts": quote.get("sim_ts"),
        }
        if mode >= 2:
            bid = float(quote.get("bid") or ltp)
            ask = float(quote.get("ask") or ltp)
            market_data.update(
                {
                    "volume": int(quote.get("volume") or 0),
                    "oi": int(quote.get("oi") or 0),
                    "open": float(quote.get("open") or ltp),
                    "high": float(quote.get("high") or ltp),
                    "low": float(quote.get("low") or ltp),
                    "close": float(quote.get("close") or ltp),
                    "bid": bid,
                    "ask": ask,
                    "bid_price": bid,
                    "ask_price": ask,
                    "bid_size": 100,
                    "ask_size": 100,
                }
            )
        if mode >= 3:
            bid = float(market_data.get("bid") or ltp)
            ask = float(market_data.get("ask") or ltp)
            market_data["depth"] = {
                "buy": [{"price": bid, "quantity": 100}],
                "sell": [{"price": ask, "quantity": 100}],
            }
        mode_str = _MODE_LABEL.get(mode, "QUOTE")
        topic = f"{exchange}_{symbol}_{mode_str}"
        self.publish_market_data(topic, market_data)
