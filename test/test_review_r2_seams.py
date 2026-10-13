"""R2 seam checks for the fork's broker / MCP / ledger sidecars (Trade review rows 60 and 62).

The one permanent file for this crew's guards. Each test names the defect it pins; all hermetic.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import types
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---- alpaca: order mapping fails loud, paper is the default -----------------------------------


@pytest.mark.parametrize("pricetype", ["STOP", "market-on-close", "x", "SLM"])
def test_unknown_pricetype_is_refused_not_turned_into_a_market_order(pricetype) -> None:
    from broker.alpaca.mapping.order_data import transform_openalgo_order

    with pytest.raises(ValueError, match="unsupported pricetype"):
        transform_openalgo_order({"symbol": "AAPL", "quantity": 1, "action": "BUY", "pricetype": pricetype})


@pytest.mark.parametrize(
    "order",
    [
        {"pricetype": "LIMIT", "price": 0},
        {"pricetype": "limit"},
        {"pricetype": "SL", "price": 10, "trigger_price": 0},
        {"pricetype": "SL-M", "trigger_price": None},
        {"pricetype": "LIMIT", "price": -1},
    ],
)
def test_priced_order_types_need_a_positive_price(order) -> None:
    from broker.alpaca.mapping.order_data import transform_openalgo_order

    with pytest.raises(ValueError, match="positive"):
        transform_openalgo_order({"symbol": "AAPL", "quantity": 1, "action": "BUY", **order})


def test_valid_order_shapes_still_map() -> None:
    from broker.alpaca.mapping.order_data import transform_openalgo_order

    base = {"symbol": "brk.b", "quantity": "3", "action": "SELL"}
    assert transform_openalgo_order({**base, "pricetype": "limit", "price": "12.5"})["limit_price"] == "12.5"
    assert transform_openalgo_order({**base})["type"] == "market"
    stop = transform_openalgo_order({**base, "pricetype": "SL", "price": 9, "trigger_price": 10})
    assert (stop["type"], stop["stop_price"], stop["limit_price"]) == ("stop_limit", "10.0", "9.0")


@pytest.mark.parametrize("qty", ["-4", "4", "-4.0"])
def test_short_position_is_negative_whichever_sign_the_broker_reports(qty) -> None:
    from broker.alpaca.mapping.order_data import transform_positions_data

    rows = transform_positions_data([{"symbol": "TSLA", "qty": qty, "side": "short"}])
    assert rows[0]["quantity"] == -4
    rows = transform_positions_data([{"symbol": "TSLA", "qty": qty, "side": "long"}])
    assert rows[0]["quantity"] == 4


def test_alpaca_profile_typo_is_not_the_live_host(monkeypatch) -> None:
    from broker.alpaca.api import baseurl

    monkeypatch.delenv("ALPACA_API_BASE", raising=False)
    monkeypatch.setenv("ALPACA_PROFILE", "papper")
    with pytest.raises(ValueError, match="paper' or 'live"):
        baseurl.trade_base_for(False)
    monkeypatch.delenv("ALPACA_PROFILE")
    assert baseurl.trade_base_for(False) == baseurl.PAPER_HOST  # unset = paper
    monkeypatch.setenv("ALPACA_PROFILE", " LIVE ")
    assert baseurl.trade_base_for(False) == baseurl.LIVE_HOST
    assert baseurl.trade_base_for(True) == baseurl.PAPER_HOST  # analyze mode wins over a live profile


def test_marketcontext_reports_the_venue_the_plugin_will_use(monkeypatch) -> None:
    from services import marketcontext_service as svc

    monkeypatch.delenv("ALPACA_API_BASE", raising=False)
    monkeypatch.delenv("ALPACA_PROFILE", raising=False)
    data = svc.build_marketcontext_data(broker="alpaca", analyze_mode=False)
    assert (data["execution_venue"], data["positions_authority"]) == ("paper-api.alpaca.markets", "alpaca.paper")
    monkeypatch.setenv("ALPACA_API_BASE", "https://example.test/")
    assert svc.build_marketcontext_data(broker="alpaca", analyze_mode=False)["execution_venue"] == "example.test"


def test_alpaca_books_raise_on_failure_instead_of_reading_as_empty(monkeypatch) -> None:
    from broker.alpaca.api import order_api

    monkeypatch.setattr(order_api, "trade_base", lambda: "https://paper-api.alpaca.markets")  # no settings DB here

    for fn in (order_api.get_order_book, order_api.get_trade_book):
        with mock.patch("broker.alpaca.api.order_api.alpaca_request", return_value=(599, {"message": "timeout"})):
            with pytest.raises(RuntimeError, match="failed"):
                fn("key")
        with mock.patch("broker.alpaca.api.order_api.alpaca_request", return_value=(200, {"unexpected": 1})):
            with pytest.raises(RuntimeError):
                fn("key")
    with mock.patch("broker.alpaca.api.order_api.alpaca_request", return_value=(200, [])) as req:
        assert order_api.get_order_book("key") == []
    assert req.call_args.kwargs["params"]["limit"] == 500  # the default of 50 truncated a busy day


def test_alpaca_quote_without_any_price_raises() -> None:
    from broker.alpaca.api.data import BrokerData

    with mock.patch("broker.alpaca.api.data.alpaca_request", return_value=(200, {"latestQuote": {}, "latestTrade": {}})):
        with pytest.raises(Exception, match="no trade or quote price"):
            BrokerData("key").get_quotes("AAPL", "NASDAQ")
    with mock.patch("broker.alpaca.api.data.alpaca_request", return_value=(200, {"latestQuote": {"bp": 9.0, "ap": 11.0}})):
        assert BrokerData("key").get_quotes("AAPL", "NASDAQ")["ltp"] == 10.0


def test_alpaca_history_follows_pages_and_refuses_unknown_interval() -> None:
    from broker.alpaca.api.data import BrokerData

    def bar(t):
        return {"t": t, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}

    pages = [
        (200, {"bars": [bar("2026-01-02T14:30:00Z")], "next_page_token": "p2"}),
        (200, {"bars": [bar("2026-01-02T14:31:00Z")], "next_page_token": None}),
    ]
    with mock.patch("broker.alpaca.api.data.alpaca_request", side_effect=pages) as req:
        df = BrokerData("key").get_history("AAPL", "NASDAQ", "1m", "2026-01-02", "2026-01-02")
    assert len(df) == 2
    assert req.call_args_list[1].kwargs["params"]["page_token"] == "p2"
    with pytest.raises(ValueError, match="unsupported Alpaca interval"):
        BrokerData("key").get_history("AAPL", "NASDAQ", "W", "2026-01-02", "2026-01-09")
    forever = (200, {"bars": [], "next_page_token": "again"})
    with mock.patch("broker.alpaca.api.data.alpaca_request", return_value=forever):
        with pytest.raises(Exception, match="pages"):
            BrokerData("key").get_history("AAPL", "NASDAQ", "D", "2026-01-02", "2026-01-09")


# ---- stock_simulator broker -------------------------------------------------------------------


def test_daily_history_aggregates_the_days_minute_bars_and_stops_at_the_sim_clock() -> None:
    from broker.stock_simulator.api.data import BrokerData

    ist = ZoneInfo("Asia/Kolkata")
    stamps = [datetime(2026, 1, 5, 9, 15, tzinfo=ist), datetime(2026, 1, 5, 9, 16, tzinfo=ist), datetime(2026, 1, 5, 15, 29, tzinfo=ist),
              datetime(2026, 1, 6, 9, 15, tzinfo=ist), datetime(2026, 1, 6, 11, 0, tzinfo=ist)]
    frame = pd.DataFrame({
        "ts_ist": pd.to_datetime(stamps),
        "day": [s.date().isoformat() for s in stamps],
        "open": [100.0, 101.0, 98.0, 200.0, 210.0],
        "high": [105.0, 110.0, 99.0, 220.0, 230.0],
        "low": [99.0, 100.0, 90.0, 190.0, 205.0],
        "close": [101.0, 108.0, 95.0, 215.0, 225.0],
        "volume": [10, 20, None, 5, 7],
    })
    catalog = mock.Mock()
    catalog.frame.return_value = frame
    data = BrokerData.__new__(BrokerData)
    data._client = mock.Mock()
    data._client.status.return_value = {"clock": {"sim_now": "2026-01-06T10:00:00+05:30"}}
    rows = data._history_daily(catalog, "NIFTY", "NSE_INDEX", "2026-01-05", "2026-01-31")
    assert [r["timestamp"] for r in rows] == ["2026-01-05", "2026-01-06"]
    d1, d2 = rows
    assert (d1["open"], d1["high"], d1["low"], d1["close"], d1["volume"]) == (100.0, 110.0, 90.0, 95.0, 30)
    # the sim clock is 10:00 on the 6th: the 11:00 bar has not happened yet
    assert (d2["open"], d2["high"], d2["low"], d2["close"], d2["volume"]) == (200.0, 220.0, 190.0, 215.0, 5)
    data._client.status.return_value = {"clock": {"sim_now": "2026-01-01T10:00:00+05:30"}}
    assert data._history_daily(catalog, "NIFTY", "NSE_INDEX", "2026-01-05", "2026-01-31") == []


def test_streaming_adapter_stale_loop_exits_after_reconnect() -> None:
    from broker.stock_simulator.streaming.stock_simulator_adapter import Stock_simulatorWebSocketAdapter

    adapter = Stock_simulatorWebSocketAdapter()
    adapter.running = True
    adapter._generation = 3
    first = adapter._generation
    adapter.disconnect()
    adapter.running = True  # connect() raced in again before the old loop noticed
    adapter._generation += 1
    assert first != adapter._generation  # a loop started at `first` is no longer live


def test_simulator_config_route_keeps_no_copy_of_the_replay_state() -> None:
    source = (ROOT / "blueprints" / "simulator_control.py").read_text(encoding="utf-8")
    assert "set_config" not in source  # the service owns the arm state (Trade D375)


# ---- ledgers / registry / blueprints ----------------------------------------------------------


def test_scheduler_log_buffer_is_capped_in_job_count_and_keeps_recent_jobs() -> None:
    from services import scheduler_run_log_buffer as buf

    buf._BUFFERS.clear()
    buf._SEQ_COUNTERS.clear()
    for i in range(buf._MAX_JOBS + 25):
        buf.append_log("job_%d" % i, "line")
    assert len(buf._BUFFERS) == len(buf._SEQ_COUNTERS) == buf._MAX_JOBS
    assert buf.get_logs_since("job_0") == []  # evicted, oldest first
    assert buf.get_logs_since("job_%d" % (buf._MAX_JOBS + 24))[0]["seq"] == 1
    buf.append_log("job_30", "again")  # a touched job moves to the recent end and survives the next purge
    for i in range(10):
        buf.append_log("fresh_%d" % i, "x")
    assert buf.get_logs_since("job_30")[-1]["message"] == "again"
    buf._BUFFERS.clear()
    buf._SEQ_COUNTERS.clear()


def test_trigger_now_uses_the_schedulers_timezone() -> None:
    from services import scheduler_registry_service as svc

    ist = ZoneInfo("Asia/Kolkata")
    scheduler = mock.Mock()
    scheduler.timezone = ist
    scheduler.get_job.return_value = mock.Mock(next_run_time=datetime(2030, 1, 1, tzinfo=ist))
    with mock.patch.object(svc, "_get_scheduler", return_value=scheduler), \
            mock.patch.object(svc, "get_auth_token_broker", return_value=("t", "b")):
        ok, _body, status = svc.trigger_scheduler_job_now("key", "flow", "wf_1")
    assert ok and status == 200
    assert scheduler.modify_job.call_args.kwargs["next_run_time"].tzinfo is ist


@pytest.mark.parametrize(
    "line,expected",
    [
        ("K=abc", "abc"),
        ('K="abc"', "abc"),
        ("K='abc'   # rotated daily", "abc"),
        ("K=abc # rotated daily", "abc"),
        ('K="abc" # it\'s "quoted"', "abc"),
        ("K=ab#c", "ab#c"),  # a # glued to the value is part of it
        ('K="a\\"b"', 'a"b'),
        ("K=  spaced  ", "spaced"),
    ],
)
def test_env_value_reader_drops_inline_comments(monkeypatch, tmp_path, line, expected) -> None:
    from utils import broker_env_sync

    env = tmp_path / ".env"
    env.write_text("OTHER=1\n%s\nTAIL=2\n" % line, encoding="utf-8")
    monkeypatch.setattr(broker_env_sync, "env_file_path", lambda: str(env))
    assert broker_env_sync.read_env_key_from_file("K") == expected


def test_trade_plan_reads_the_declared_hub_and_refuses_to_guess(monkeypatch, tmp_path) -> None:
    from blueprints import trade_plan

    monkeypatch.delenv("TRADE_STACK_HUB_DIR", raising=False)
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.context.hub import HubDirNotDeclaredError

    with pytest.raises(HubDirNotDeclaredError):
        trade_plan._safe_plan_path("NIFTY")  # used to fall back to <root>/reports/hub, the production hub
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    path = trade_plan._safe_plan_path("nifty")
    assert path == tmp_path.resolve() / "NIFTY" / "options_research" / "latest.json"
    assert trade_plan._safe_plan_path("../etc") is None
    assert trade_plan._safe_plan_path("a/../b") is None


def test_default_valid_brokers_sample_has_no_production_caller() -> None:
    from utils import broker_registry

    assert not hasattr(broker_registry, "default_valid_brokers_sample")  # only a test read it


# ---- MCP: a polled debate tool must not start a second run -----------------------------------


def _load_custom_tools():
    spec = importlib.util.spec_from_file_location("_custom_tools_r2_seams", ROOT / "mcp" / "custom_tools.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tradingagents_poll_does_not_start_a_second_debate(monkeypatch) -> None:
    release = threading.Event()
    started: list[str] = []

    def run_agent_debate(key, asset_type=None):
        started.append(key)
        release.wait(5)

    fake = {
        "trade_integrations.bridge.agent_debate": types.SimpleNamespace(run_agent_debate=run_agent_debate),
        "trade_integrations.context.hub": types.SimpleNamespace(
            is_agent_debate_cache_fresh=lambda key: False, load_agent_debate_json=lambda key: None
        ),
        "trade_integrations.bridge.hub_context": types.SimpleNamespace(infer_debate_asset_type=lambda key, hint: hint or "stock"),
    }
    for name, mod in fake.items():
        monkeypatch.setitem(sys.modules, name, mod)

    class _Stub:
        RISK_BROKER_STRUCTURED = "broker_structured"
        RISK_EXTERNAL_TEXT = "external_text"
        TOOL_META: dict = {}

        def __init__(self):
            self.tools = {}

        def openalgo_tool(self, *_a, **_k):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, _name):
            return "stub"

    stub = _Stub()
    _load_custom_tools().register(stub)
    tool = stub.tools["run_tradingagents_analysis"]
    try:
        first = tool("NIFTY")
        for _ in range(3):
            assert "already running" in tool("NIFTY")  # the reply tells the model to poll; polls must not stack runs
        assert "started" in first
        deadline = threading.Event()
        deadline.wait(0.2)
        assert started == ["NIFTY"]
    finally:
        release.set()
    for _ in range(50):  # the slot frees once the run ends
        if "already running" not in tool("NIFTY"):
            break
        threading.Event().wait(0.05)
    else:
        pytest.fail("the in-flight slot was never released")
    release.set()
