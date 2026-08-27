"""Custom MCP tools layered on top of openalgo's mcp/mcpserver.py.

These are tools we added on top of the upstream mcp/mcpserver.py. They
live here, not in mcpserver.py itself, so re-syncing with upstream never
has to reconcile our tool additions against upstream's own edits to that
file. mcpserver.py loads this file by path and calls register() right
before it finalizes its tool registry -- see the loader block near
_finalize_registry() in mcpserver.py for why (mcp/ is not a package, and
the name collides with the pip-installed ``mcp`` SDK, so this can't be a
normal import).
"""

import json
import os
import sys
from typing import Any


def _get_logger():
    """Import openalgo's own centralized logger lazily.

    This module is loaded via ``importlib.util.spec_from_file_location`` by
    ``mcp/mcpserver.py`` (see the module docstring), so ``sys.path[0]`` at
    import time is this file's own directory (``mcp/``), not the openalgo
    package root -- a plain ``from utils.logging import get_logger`` fails
    with ``ModuleNotFoundError`` in that context even though it works when
    this file happens to be run with the openalgo root as cwd. Insert the
    openalgo root explicitly first so the import is reliable regardless of
    how this module got loaded.
    """
    from pathlib import Path

    openalgo_root = str(Path(__file__).resolve().parent.parent)
    if openalgo_root not in sys.path:
        sys.path.insert(0, openalgo_root)
    from utils.logging import get_logger

    return get_logger("openalgo_mcp_custom_tools")


logger = _get_logger()


def register(mcpserver):
    """Register our custom MCP tools onto the shared FastMCP instance.

    `mcpserver` is the (partially-initialized) mcp/mcpserver.py module
    object, exposing the ``mcp`` FastMCP instance our @mcp.tool()
    decorators below register against.
    """
    mcp = mcpserver.mcp

    @mcp.tool()
    def get_us_quote(symbol: str) -> str:
        """
        Get a near-real-time US equity quote via Alpaca paper/live data API.

        Requires ALPACA_API_KEY and ALPACA_API_SECRET in the trade stack .env.
        Uses IEX feed by default (ALPACA_DATA_FEED=iex).

        Args:
            symbol: US ticker (e.g. AAPL, MSFT, BRK-B)

        Returns:
            JSON with ltp, bid, ask, feed, and profile (paper/live).
        """
        try:
            from trade_integrations.dataflows.alpaca import (
                alpaca_configured,
                fetch_alpaca_quote,
                fetch_alpaca_trade_snapshot,
            )

            if not alpaca_configured():
                return (
                    "Error: Alpaca is not configured. Set ALPACA_API_KEY and "
                    "ALPACA_API_SECRET in the trade stack .env file."
                )
            clean = symbol.strip().upper()
            quote = fetch_alpaca_quote(clean)
            if quote and quote.get("ltp") is not None:
                return json.dumps(quote, indent=2, default=str)
            snap = fetch_alpaca_trade_snapshot(clean)
            if snap:
                return json.dumps(snap, indent=2, default=str)
            return f"Error: no Alpaca quote available for {clean}"
        except Exception as e:
            logger.exception("get_us_quote failed: %s", e)
            return f"Error getting US quote: {str(e)}"


    @mcp.tool()
    def get_us_paper_account() -> str:
        """
        Fetch Alpaca paper trading account summary (cash, equity, buying power).

        Requires ALPACA_API_KEY / ALPACA_API_SECRET with ALPACA_PROFILE=paper.
        """
        try:
            from trade_integrations.dataflows.alpaca import alpaca_configured, fetch_alpaca_account

            if not alpaca_configured():
                return (
                    "Error: Alpaca is not configured. Set ALPACA_API_KEY and "
                    "ALPACA_API_SECRET in the trade stack .env file."
                )
            return json.dumps(fetch_alpaca_account(), indent=2, default=str)
        except Exception as e:
            logger.exception("get_us_paper_account failed: %s", e)
            return f"Error getting Alpaca account: {str(e)}"

    def _ensure_trade_stack_import() -> None:
        """Prepare sys.path and skip TradingAgents graph patches for MCP tools."""
        from pathlib import Path

        os.environ.setdefault("TRADE_INTEGRATIONS_SKIP_APPLY", "1")
        trade_root = Path(__file__).resolve().parents[2]
        integrations = trade_root / "integrations"
        tradingagents = trade_root / "tradingagents"
        for path in (integrations, tradingagents):
            if path.is_dir() and str(path) not in sys.path:
                sys.path.insert(0, str(path))


    def _import_payoff_charges():
        """Load trade-stack payoff/charges helpers when the repo is co-located."""
        _ensure_trade_stack_import()
        from trade_integrations.dataflows.options_research.payoff_charges import (
            calculate_charges,
            compute_payoff,
            estimate_strategy_metrics,
        )

        return compute_payoff, calculate_charges, estimate_strategy_metrics


    def _import_options_research():
        """Load trade-stack options browse + plan helpers when co-located."""
        _ensure_trade_stack_import()
        from trade_integrations.dataflows.options_research.browse_summary import (
            build_browse_summary,
            format_browse_markdown,
        )
        from trade_integrations.tools.options_research_tools import fetch_options_research_report

        return build_browse_summary, format_browse_markdown, fetch_options_research_report


    def _import_module_from_file(module_name: str, file_path: str):
        """Import a single .py file without triggering trade_integrations package init."""
        import importlib.util
        from pathlib import Path

        path = Path(file_path)
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


    def _import_browse_summary_lightweight():
        """Browse helpers without pulling the full trade_integrations stack."""
        from pathlib import Path

        trade_root = Path(__file__).resolve().parents[2]
        browse_path = trade_root / "integrations/trade_integrations/dataflows/options_research/browse_summary.py"
        mod = _import_module_from_file("_oa_browse_summary", str(browse_path))
        return mod.build_browse_summary, mod.format_browse_markdown


    def _import_json_safe_lightweight():
        from pathlib import Path

        trade_root = Path(__file__).resolve().parents[2]
        path = trade_root / "integrations/trade_integrations/dataflows/json_safe.py"
        mod = _import_module_from_file("_oa_json_safe", str(path))
        return mod.json_safe


    def _normalize_openalgo_expiry(expiry: str) -> str:
        return expiry.strip().upper().replace("-", "")


    def _unwrap_optionchain_response(response: dict[str, Any]) -> dict[str, Any]:
        data = response.get("data")
        if isinstance(data, dict) and data.get("chain"):
            return data
        if response.get("chain"):
            return response
        if isinstance(data, list):
            return {"chain": data}
        return data if isinstance(data, dict) else {}


    def _chain_snapshot_via_hub_channel(
        underlying: str,
        exchange: str,
        *,
        expiry_date: str | None = None,
        strike_count: int | None = None,
    ) -> dict[str, Any]:
        """Build chain snapshot through hub channel (read-first + write-through)."""
        _ensure_trade_stack_import()
        from trade_integrations.openalgo.market_data import fetch_option_chain_channel_vendor
        from trade_integrations.hub_capture.channel import get_chain

        return get_chain(
            underlying,
            exchange,
            fetch_option_chain_channel_vendor,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )


    def _fetch_expiries_via_channel(underlying: str, options_exchange: str) -> list[str]:
        _ensure_trade_stack_import()
        from trade_integrations.openalgo.market_data import fetch_option_expiry_dates

        return fetch_option_expiry_dates(underlying, options_exchange)


    def _import_stock_research():
        _ensure_trade_stack_import()
        from trade_integrations.dataflows.stock_research.browse_summary import (
            build_stock_browse_summary,
            format_stock_browse_markdown,
        )
        from trade_integrations.dataflows.stock_research.aggregator import run_stock_research
        from trade_integrations.dataflows.stock_research.format import format_stock_report
        from trade_integrations.context.hub import load_stock_research_json, save_stock_research

        return (
            build_stock_browse_summary,
            format_stock_browse_markdown,
            run_stock_research,
            format_stock_report,
            load_stock_research_json,
            save_stock_research,
        )


    def _import_index_research():
        _ensure_trade_stack_import()
        from trade_integrations.context.hub import load_index_research_json, save_index_research
        from trade_integrations.dataflows.index_research.aggregator import run_index_research
        from trade_integrations.dataflows.index_research.format import format_index_report
        from trade_integrations.tools.index_research_tools import fetch_index_research_report

        return (
            run_index_research,
            format_index_report,
            load_index_research_json,
            save_index_research,
            fetch_index_research_report,
        )


    @mcp.tool()
    def get_strategy_payoff(
        legs: list[dict[str, Any]],
        spot: float,
        range_pct: float = 0.12,
        steps: int = 80,
        expiry_date: str | None = None,
        iv: float | None = None,
    ) -> str:
        """
        Compute expiry payoff curve for a multi-leg options strategy.

        Args:
            legs: Strategy legs with side (BUY/SELL), strike, option_type (CE/PE),
                  price, quantity (or lot_size * lots), symbol optional.
            spot: Current underlying price for sampling range.
            range_pct: Underlying range as fraction of spot (default 12%).
            steps: Number of payoff samples.

            expiry_date: Optional expiry DDMMMYY for OptionLab PoP.
            iv: Optional ATM IV (percent) for OptionLab PoP.

        Returns:
            JSON with samples, breakevens, max_profit, max_loss, pop, and net P&L fields.
        """
        try:
            _, _, estimate_strategy_metrics = _import_payoff_charges()
            result = estimate_strategy_metrics(
                legs,
                spot=spot,
                expiry=expiry_date,
                iv=iv,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("get_strategy_payoff failed: %s", e)
            return f"Error computing strategy payoff: {str(e)}"


    @mcp.tool()
    def get_trade_charges(
        legs: list[dict[str, Any]],
        broker_preset: str | None = None,
    ) -> str:
        """
        Estimate India F&O charges per leg and portfolio total.

        Args:
            legs: Strategy legs with side, price, quantity (or lot_size * lots).
            broker_preset: Charge model preset (default: OpenAlgo session / indmoney).

        Returns:
            JSON with per_leg breakdown and total (brokerage, STT, GST, stamp, exchange).
        """
        try:
            from trade_integrations.research.broker_context import resolve_broker_preset

            _, calculate_charges, _ = _import_payoff_charges()
            broker = broker_preset or resolve_broker_preset()
            result = calculate_charges(legs, broker_preset=broker)
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("get_trade_charges failed: %s", e)
            return f"Error calculating trade charges: {str(e)}"


    @mcp.tool()
    def get_options_browse(
        underlying: str,
        exchange: str,
        expiry_date: str | None = None,
        strike_count: int = 10,
    ) -> str:
        """
        Compact in-chat browse of a live India options chain (expiries, ATM, top strikes).

        Use this first when the user asks what options are available before loading a full trade plan.

        Args:
            underlying: Index or stock symbol (e.g. NIFTY, RELIANCE)
            exchange: NSE_INDEX, BSE_INDEX, NSE, or BSE
            expiry_date: Optional expiry DDMMMYY
            strike_count: Strikes above/below ATM (default 10)

        Returns:
            JSON with browse_summary and markdown (table for chat).
        """
        try:
            build_browse_summary, format_browse_markdown = _import_browse_summary_lightweight()
            json_safe = _import_json_safe_lightweight()

            chain_snapshot = _chain_snapshot_via_hub_channel(
                underlying,
                exchange,
                expiry_date=expiry_date,
                strike_count=strike_count,
            )
            if not chain_snapshot.get("expiry_date"):
                options_exchange = "NFO" if exchange.upper() in ("NSE", "NSE_INDEX") else "BFO"
                expiries = _fetch_expiries_via_channel(underlying, options_exchange)
                if expiries and not expiry_date:
                    chain_snapshot = _chain_snapshot_via_hub_channel(
                        underlying,
                        exchange,
                        expiry_date=_normalize_openalgo_expiry(expiries[0]),
                        strike_count=strike_count,
                    )
            options_exchange = "NFO" if exchange.upper() in ("NSE", "NSE_INDEX") else "BFO"
            expiries = _fetch_expiries_via_channel(underlying, options_exchange)
            chain_snapshot["expiries"] = [_normalize_openalgo_expiry(e) for e in expiries]

            summary = build_browse_summary(chain_snapshot)
            payload = {
                "browse_summary": json_safe(summary),
                "markdown": format_browse_markdown(summary),
            }
            return json.dumps(payload, indent=2, default=str)
        except Exception as e:
            logger.exception("get_options_browse failed: %s", e)
            return f"Error browsing options chain: {str(e)}"


    @mcp.tool()
    def get_options_trade_plan(
        ticker: str,
        refresh: bool = False,
        expiry_date: str | None = None,
        lookahead_days: int | None = None,
    ) -> str:
        """
        Load or generate the full options trade plan from the trade-stack hub.

        Includes prediction, events, ranked strategies, recommended legs, payoff,
        charges, and implementation steps. Set refresh=true to bypass cache.

        Args:
            ticker: Underlying (NIFTY, BANKNIFTY, RELIANCE, …)
            refresh: When true, regenerate even if hub cache is fresh
            expiry_date: Optional expiry DDMMMYY
            lookahead_days: Event lookahead window (default from env)

        Returns:
            Markdown trade plan ready for agent explanation.
        """
        try:
            _, _, fetch_options_research_report = _import_options_research()
            report = fetch_options_research_report(
                ticker,
                expiry_date=expiry_date,
                lookahead_days=lookahead_days,
                use_cache=not refresh,
            )
            return report
        except Exception as e:
            logger.exception("get_options_trade_plan failed: %s", e)
            return f"Error loading options trade plan: {str(e)}"


    def _trade_widget_store_dir():
        from pathlib import Path

        root = Path.home() / ".vibe-trading" / "trade_widgets"
        root.mkdir(parents=True, exist_ok=True)
        return root


    @mcp.tool()
    def get_options_trade_widget(
        ticker: str,
        refresh: bool = False,
        expiry_date: str | None = None,
        lookahead_days: int | None = None,
    ) -> str:
        """
        Build a structured trade-plan widget for Vibe chat (scenarios, payoff chart data,
        charges, recommended legs, execute steps).

        Call when presenting ranked strategy options or a recommended plan with legs — not for
        browse-only, prediction-only, or event summaries without actionable strategies.

        Returns JSON with type ``trade_plan.widget``. The Vibe UI renders this as an
        interactive card with payoff graph and Execute button.

        Args:
            ticker: Underlying (NIFTY, RELIANCE, AAPL, …)
            refresh: Regenerate hub plan before building widget
            expiry_date: Optional expiry DDMMMYY
            lookahead_days: Event lookahead window

        Returns:
            JSON widget payload (also persisted under ~/.vibe-trading/trade_widgets/).
        """
        try:
            _ensure_trade_stack_import()
            from trade_integrations.dataflows.options_research.widget_payload import (
                build_options_trade_widget,
            )

            widget = build_options_trade_widget(
                ticker,
                expiry_date=expiry_date,
                lookahead_days=lookahead_days,
                refresh=refresh,
            )
            widget_id = widget.get("widget_id")
            if widget_id:
                store = _trade_widget_store_dir() / f"{widget_id}.json"
                store.write_text(json.dumps(widget, indent=2, default=str), encoding="utf-8")
            return json.dumps(widget, indent=2, default=str)
        except Exception as e:
            logger.exception("get_options_trade_widget failed: %s", e)
            return json.dumps(
                {"type": "trade_plan.widget", "error": str(e), "underlying": ticker},
                indent=2,
            )


    @mcp.tool()
    def get_plan_position_status(widget_id: str) -> str:
        """
        Return execution ledger entry and matched broker positions for a trade widget.

        Gated by OPTIONS_REALTIME_MONITOR_ENABLED for legacy paths; always returns
        ledger + thesis-break when a ledger entry exists (for autonomous agent trading).

        Args:
            widget_id: Persisted trade-plan widget id (tp_*)

        Returns:
            JSON with ledger entry, matched positions, thesis-break report, and position P&L.
        """
        try:
            _ensure_trade_stack_import()
            from trade_integrations.monitor.config import is_monitor_enabled
            from trade_integrations.context.hub import load_options_research_json
            from trade_integrations.monitor.execution_ledger import (
                fetch_position_book,
                get_ledger_entry,
                match_positions_for_entry,
            )
            from trade_integrations.monitor.live_quotes import fetch_underlying_ltp
            from trade_integrations.monitor.thesis_break import evaluate_thesis_break

            ledger_entry = get_ledger_entry(widget_id)
            if ledger_entry is None:
                return json.dumps({"widget_id": widget_id, "ledger": None})

            position_book = fetch_position_book()
            matched_positions, position_pnl = match_positions_for_entry(
                ledger_entry,
                position_book or {},
            )
            underlying = str(ledger_entry.get("underlying") or "").strip().upper()
            doc = load_options_research_json(underlying) if underlying else None
            live_spot = fetch_underlying_ltp(underlying) if underlying else None
            thesis_report = evaluate_thesis_break(
                doc,
                ledger_entry,
                live_spot=live_spot,
                position_pnl=position_pnl,
            )
            payload: dict[str, Any] = {
                "widget_id": widget_id,
                "ledger": ledger_entry,
                "matched_positions": matched_positions,
                "position_pnl": position_pnl,
                "monitor_enabled": is_monitor_enabled(),
            }
            payload["thesis_break"] = {
                "broken": thesis_report.broken,
                "reasons": thesis_report.reasons,
                "severity": thesis_report.severity,
                "live_spot": thesis_report.live_spot,
                "plan_spot": thesis_report.plan_spot,
                "position_pnl": thesis_report.position_pnl,
            }
            return json.dumps(payload, indent=2, default=str)
        except Exception as e:
            logger.exception("get_plan_position_status failed: %s", e)
            return json.dumps({"widget_id": widget_id, "error": str(e)}, indent=2)


    def _import_autonomous_agents():
        _ensure_trade_stack_import()
        from trade_integrations.autonomous_agents import mcp_actions

        return mcp_actions


    def _coerce_mcp_dict(value: Any) -> dict[str, Any] | None:
        """Accept dict or JSON string from LLM tool calls."""
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("expected JSON object")
            return parsed
        raise ValueError(f"expected dict or JSON string, got {type(value).__name__}")


    @mcp.tool()
    def stop_autonomous_agents() -> str:
        """
        Stop all running autonomous agents and remove obsolete standalone cron jobs.

        Use when the user asks to stop autonomous trading or end all agent sessions.
        Does not close open positions — flatten via bridge/OpenAlgo first if needed.
        """
        try:
            actions = _import_autonomous_agents()
            return json.dumps(actions.mcp_stop_running_agents(), indent=2, default=str)
        except Exception as e:
            logger.exception("stop_autonomous_agents failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def get_autonomous_market_feedback(
        agent_id: str | None = None,
        ticker: str | None = None,
    ) -> str:
        """
        Live market feedback for an autonomous agent turn.

        Returns spot drift, material news, open position P&L, thesis-break alerts,
        and deltas since the last agent turn.
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_get_market_feedback(agent_id=agent_id, ticker=ticker)
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("get_autonomous_market_feedback failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def execute_autonomous_basket(
        widget_id: str,
        agent_id: str | None = None,
        confidence: int | None = None,
    ) -> str:
        """
        Execute a researched trade-plan widget for an autonomous agent (OpenAlgo paper/analyzer).

        Call after plan validation when the agent decides to ENTER.
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_execute_basket(widget_id, agent_id=agent_id, confidence=confidence)
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("execute_autonomous_basket failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def propose_autonomous_agent(
        symbols: list[str],
        name: str | None = None,
        mandate: str | None = None,
        budget_inr: float | None = None,
        max_daily_loss_inr: float | None = None,
        confidence_threshold: int | None = None,
        watch_interval_min: int | None = None,
        research_interval_min: int | None = None,
        mode: str = "paper",
        execution_market: str | None = None,
        user_text: str | None = None,
        allowed_instruments: list[str] | None = None,
        vibe_session_id: str | None = None,
    ) -> str:
        """
        Propose a persistent autonomous trading agent (read-only — user confirms in UI).

        Creates a proposal card; does NOT start the agent until the user clicks Confirm.

        Args:
            symbols: Symbols to watch/trade (e.g. ["NIFTY"])
            name: Display name
            mandate: Trading goal / constraints in plain language
            budget_inr: Paper budget (default 20000)
            max_daily_loss_inr: Daily loss halt (default 2000)
            confidence_threshold: Act when confidence >= this (default 75)
            watch_interval_min: News/market watch cadence (default 7 min)
            research_interval_min: Full reasoning cadence (default 90 min)
            mode: paper only in v1
            execution_market: Optional IN or US override when user explicitly chose market
            user_text: Original user message for market hint resolution
            allowed_instruments: equity and/or options — omit to auto-infer (RELIANCE defaults equity)
            vibe_session_id: Orchestrator chat session id

        Returns:
            JSON with status, proposal_id, missing_fields, and proposal when ready.
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_propose(
                symbols=symbols,
                name=name,
                mandate=mandate,
                budget_inr=budget_inr,
                max_daily_loss_inr=max_daily_loss_inr,
                confidence_threshold=confidence_threshold,
                watch_interval_min=watch_interval_min,
                research_interval_min=research_interval_min,
                mode=mode,
                execution_market=execution_market,
                user_text=user_text,
                allowed_instruments=allowed_instruments,
                orchestrator_session_id=vibe_session_id,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("propose_autonomous_agent failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def get_autonomous_agent_status(agent_id: str | None = None) -> str:
        """
        Get status of one autonomous agent or list all agents.

        Args:
            agent_id: Optional aa_* id; omit to list all

        Returns:
            JSON agent state or agent list.
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_get_status(agent_id=agent_id)
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("get_autonomous_agent_status failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def record_autonomous_decision(
        agent_id: str,
        decision: str,
        rationale: str,
        ticker: str | None = None,
        actions_taken: list[str] | None = None,
        confidence: int | None = None,
        direction: str | None = None,
        strategy: str | None = None,
    ) -> str:
        """
        Log an autonomous agent decision (ENTER/REVISE/EXIT/HOLD/SKIP).

        Updates the agent instance thesis (direction, strategy, confidence, rationale).
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_record_decision(
                agent_id=agent_id,
                decision=decision,
                rationale=rationale,
                ticker=ticker,
                actions_taken=actions_taken,
                confidence=confidence,
                direction=direction,
                strategy=strategy,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("record_autonomous_decision failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def set_agent_watch_spec(
        agent_id: str,
        watch_spec: dict | str | None = None,
        strategy: str | None = None,
        spot_move_pct: float | None = None,
        cooldown_sec: int | None = None,
        skip_if_unchanged_minutes: int | None = None,
        spot: float | None = None,
        target: float | None = None,
        stop: float | None = None,
    ) -> str:
        """
        Persist Nautilus-compatible watch rules on an autonomous agent instance.

        Prefer `strategy` with scalar params — e.g.
        `set_agent_watch_spec(agent_id=..., strategy="hold_cash", spot_move_pct=0.009)`.
        Backend derives rules from strategy; do not pass raw nested `watch_spec` unless necessary.

        Args:
            agent_id: aa_* agent id
            watch_spec: optional explicit {rules: [...], gate: {...}, cooldown_sec: 300} (dict or JSON string)
            strategy: recommended strategy name — rules derived automatically
            spot_move_pct: override spot-move alert threshold (%)
            cooldown_sec: seconds between repeated alerts
            skip_if_unchanged_minutes: gate — skip alert when spot unchanged for N minutes
            spot: current spot price (for N-point mandate conversion and level rules)
            target: optional dip/target level for strategy-derived rules
            stop: optional stop level for strategy-derived rules
        """
        try:
            watch_spec = _coerce_mcp_dict(watch_spec)
            actions = _import_autonomous_agents()
            result = actions.mcp_set_watch_spec(
                agent_id=agent_id,
                watch_spec=watch_spec,
                strategy=strategy,
                spot_move_pct=spot_move_pct,
                cooldown_sec=cooldown_sec,
                skip_if_unchanged_minutes=skip_if_unchanged_minutes,
                spot=spot,
                target=target,
                stop=stop,
            )
            return json.dumps(result, indent=2, default=str)
        except json.JSONDecodeError as e:
            return json.dumps(
                {"status": "error", "error": f"watch_spec JSON invalid or truncated: {e}"},
                indent=2,
            )
        except Exception as e:
            logger.exception("set_agent_watch_spec failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def list_watches(
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> str:
        """
        List active Nautilus watches for an interactive session or autonomous agent.

        Args:
            session_id: Vibe session id for /agent watches
            agent_id: aa_* autonomous agent id
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_list_watches(session_id=session_id, agent_id=agent_id)
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("list_watches failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def create_session_watch(
        session_id: str,
        watch_spec: dict | str,
        symbols: list[str] | None = None,
        label: str | None = None,
        one_shot: bool = False,
    ) -> str:
        """
        Create a Nautilus watch bound to an interactive /agent session (owner ws_{session_id}).

        Args:
            session_id: Vibe session id
            watch_spec: {rules: [...], cooldown_sec: 300} (dict or JSON string)
            symbols: optional symbol list; derived from rules when omitted
            label: optional display label
            one_shot: delete watch after first alert fires
        """
        try:
            watch_spec = _coerce_mcp_dict(watch_spec)
            if not watch_spec:
                return json.dumps({"status": "error", "error": "watch_spec is required"}, indent=2)
            actions = _import_autonomous_agents()
            result = actions.mcp_create_session_watch(
                session_id=session_id,
                watch_spec=watch_spec,
                symbols=symbols,
                label=label,
                one_shot=one_shot,
            )
            return json.dumps(result, indent=2, default=str)
        except json.JSONDecodeError as e:
            return json.dumps(
                {"status": "error", "error": f"watch_spec JSON invalid or truncated: {e}"},
                indent=2,
            )
        except Exception as e:
            logger.exception("create_session_watch failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def delete_watch(watch_id: str) -> str:
        """
        Delete (deactivate) a watch by watch_id from the unified registry.

        Args:
            watch_id: w_* id from list_watches or create_session_watch
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_delete_watch(watch_id=watch_id)
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("delete_watch failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def get_quant_monitor_status(agent_id: str) -> str:
        """
        Quant monitor snapshot for an autonomous agent (profile, baselines, last alert).

        Args:
            agent_id: aa_* agent id

        Returns:
            JSON with quant_state and last_quant_alert_at.
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_get_quant_monitor_status(agent_id=agent_id)
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("get_quant_monitor_status failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def submit_bridge_execution_intent(
        agent_id: str,
        action: str,
        rationale: str,
        widget_id: str | None = None,
        underlying: str | None = None,
    ) -> str:
        """
        Submit an execution intent for India autonomous agents (bridge → OpenAlgo).

        Use for EXIT/ADJUST when not using execute_autonomous_basket.
        ENTER with legs typically goes through execute_autonomous_basket instead.
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_submit_bridge_execution_intent(
                agent_id=agent_id,
                action=action,
                rationale=rationale,
                widget_id=widget_id,
                underlying=underlying,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("submit_bridge_execution_intent failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def submit_partial_close(
        agent_id: str,
        fraction: float,
        rationale: str,
        underlying: str | None = None,
    ) -> str:
        """
        Reduce (not flatten) an India autonomous agent's open position by `fraction`.

        Use when the position should carry less risk but the thesis isn't dead yet —
        e.g. after a drawdown-duration or trailing-stop review flags sustained loss.
        For a full flatten use submit_bridge_execution_intent with action="EXIT".

        Args:
            agent_id: aa_* agent id
            fraction: strictly between 0 and 1 (e.g. 0.5 halves the position)
            rationale: why this reduction is being made
            underlying: optional, defaults to the agent's own handoff/primary symbol
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_submit_partial_close(
                agent_id=agent_id,
                fraction=fraction,
                rationale=rationale,
                underlying=underlying,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("submit_partial_close failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def submit_hedge(
        agent_id: str,
        rationale: str,
        underlying: str | None = None,
        expiry: str | None = None,
        hedge_ratio: float = 1.0,
        mode: str = "short",
    ) -> str:
        """
        Buy protective legs against an India autonomous agent's open option positions.

        mode="short" (default): use when a short option's risk needs capping — this turns
        an open short into a defined-risk spread by buying a further-OTM option of the same
        type, picked from the live chain.

        mode="long": use when a long option (e.g. long calls) still carries real directional
        risk worth offsetting even though its max loss is already capped at premium paid —
        this buys a further-OTM option of the *opposite* type (e.g. a protective put against
        long calls), picked from the live chain against the long leg's own strike.

        For reducing position size instead, use submit_partial_close.

        Args:
            agent_id: aa_* agent id
            rationale: why this hedge is being placed
            underlying: optional, defaults to the agent's own handoff/primary symbol
            expiry: optional ISO date (YYYY-MM-DD); defaults to the nearest expiry among
                the agent's open short/long legs (per mode)
            hedge_ratio: fraction of each leg's quantity to hedge, in (0, 1] — 1.0 fully
                covers it
            mode: "short" (protect open SHORT legs, default) or "long" (delta-offset open
                LONG legs)
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_submit_hedge(
                agent_id=agent_id,
                rationale=rationale,
                underlying=underlying,
                expiry=expiry,
                hedge_ratio=hedge_ratio,
                mode=mode,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("submit_hedge failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def submit_roll(
        agent_id: str,
        far_expiry: str,
        rationale: str,
        underlying: str | None = None,
        near_expiry: str | None = None,
    ) -> str:
        """
        Calendar-roll an India autonomous agent's open SHORT option leg(s) forward.

        Closes the near-expiry short leg and opens a same-strike short leg at
        far_expiry, from the live chain fetched for that expiry. Use when a short
        option is running out of time (near expiry, low remaining theta) but the
        thesis still holds and premium should keep being collected further out —
        for reducing size instead use submit_partial_close, for capping risk on the
        existing leg without changing its expiry use submit_hedge.

        Deliberately scoped to same-strike calendar rolls (same strike, later
        expiry). For a strike change at the same expiry, use submit_strike_roll instead.

        Args:
            agent_id: aa_* agent id
            far_expiry: ISO date (YYYY-MM-DD) of the expiry to roll into — required,
                no default (weekly vs monthly, liquidity, DTE preference all vary,
                so this must be an explicit choice)
            rationale: why this roll is being made
            underlying: optional, defaults to the agent's own handoff/primary symbol
            near_expiry: optional ISO date (YYYY-MM-DD) of the expiry to roll away
                from; defaults to the nearest expiry among the agent's open short
                legs. Must be strictly before far_expiry.
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_submit_roll(
                agent_id=agent_id,
                far_expiry=far_expiry,
                rationale=rationale,
                underlying=underlying,
                near_expiry=near_expiry,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("submit_roll failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def submit_strike_roll(
        agent_id: str,
        rationale: str,
        underlying: str | None = None,
        expiry: str | None = None,
        min_distance_sigma: float = 1.5,
    ) -> str:
        """
        Strike-roll an India autonomous agent's open SHORT option leg(s).

        Closes the short leg and opens a same-type SELL at a further-OTM strike
        of the *same* expiry, picked from the live chain. Use when the
        underlying has moved and the short strike needs to move with it
        without changing expiry — for changing expiry instead use submit_roll,
        for capping risk on the existing leg/strike use submit_hedge.

        Deliberately scoped to same-expiry strike rolls. An expiry change is
        not supported by this tool.

        Args:
            agent_id: aa_* agent id
            rationale: why this roll is being made
            underlying: optional, defaults to the agent's own handoff/primary symbol
            expiry: optional ISO date (YYYY-MM-DD) of the short legs to roll;
                defaults to the nearest expiry among the agent's open short legs
            min_distance_sigma: minimum OTM distance (in strike-price sigma) the
                new strike must clear; a leg with no strike meeting this bound
                is skipped rather than force-rolled
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_submit_strike_roll(
                agent_id=agent_id,
                rationale=rationale,
                underlying=underlying,
                expiry=expiry,
                min_distance_sigma=min_distance_sigma,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("submit_strike_roll failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def get_portfolio_greeks(agent_id: str) -> str:
        """
        Net delta/gamma/theta/vega across an India autonomous agent's whole open
        option book (all underlyings, not just the agent's primary focus symbol).

        Use to see whole-book exposure before deciding whether individual
        positions should be adjusted together rather than one at a time.

        Args:
            agent_id: aa_* agent id
        """
        try:
            actions = _import_autonomous_agents()
            result = actions.mcp_get_portfolio_greeks(agent_id=agent_id)
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.exception("get_portfolio_greeks failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def get_research_status(
        ticker: str,
        asset_type: str = "stock",
    ) -> str:
        """
        Check unified research pipeline stage completion for a ticker.

        Args:
            ticker: Symbol (RELIANCE, NIFTY, …)
            asset_type: stock, options, or index

        Returns:
            JSON with status, stages checklist, missing fields, debate_pending.
        """
        try:
            from trade_integrations.research.orchestrator import get_research_status as _status
            from trade_integrations.research.registry import ResearchKind

            kind_map = {
                "stock": ResearchKind.STOCK,
                "options": ResearchKind.OPTIONS,
                "index": ResearchKind.INDEX,
            }
            kind = kind_map.get(asset_type.strip().lower(), ResearchKind.STOCK)
            return json.dumps(_status(ticker, kind=kind), indent=2, default=str)
        except Exception as e:
            logger.exception("get_research_status failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def get_nse_browser_status() -> str:
        """
        Read hub status for NSE/NSDL browser datasets (nodriver module).

        Returns JSON with per-dataset row counts, freshness, last mission status, and agent config.
        Does not refresh data — use get_nse_browser_data to fetch and return rows.
        """
        try:
            _ensure_trade_stack_import()
            from trade_integrations.tools.nse_browser_tools import query_nse_browser_status as _status

            return _status()
        except Exception as e:
            logger.exception("get_nse_browser_status failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def get_nse_browser_data(
        dataset: str = "fii_dii",
        start_date: str | None = None,
        end_date: str | None = None,
        refresh: bool = False,
        refresh_cookies: bool = False,
        agent_fallback: bool = True,
        backfill_historical: bool = False,
        limit: int = 500,
    ) -> str:
        """
        Fetch NSE/NSDL data not available via simple APIs (primary agent tool).

        Reads hub cache first; browses NSE/NSDL via nodriver only when stale or refresh=True.
        Returns parsed rows in JSON and persists to hub parquet under reports/hub/_data/nse_browser/.

        Agent routing:
        - FII/DII / institutional / fiidii flows → dataset=\"fii_dii\"
        - FPI / NSDL foreign portfolio → dataset=\"fpi\"
        - Bulk or block deals → dataset=\"bulk_deals\"
        - Delivery position → dataset=\"delivery\"
        - Index PE/PB → dataset=\"pe_pb\"

        Args:
            dataset: fii_dii | fpi | bulk_deals | delivery | pe_pb (aliases accepted: fii, dii, nsdl)
            start_date: YYYY-MM-DD (default ~30 days ago)
            end_date: YYYY-MM-DD (default today)
            refresh: Force live browser fetch even if cache is fresh
            refresh_cookies: Bootstrap nodriver session before fetch
            agent_fallback: MiniMax browser operator when navigation fails
            backfill_historical: Full historical CSV/archives backfill (~120s, headed browser)
            limit: Max rows returned

        Returns:
            JSON with status, records[], summary, freshness, hub_paths, mission_result.
        """
        try:
            _ensure_trade_stack_import()
            from trade_integrations.tools.nse_browser_tools import query_nse_browser_data as _get

            return _get(
                dataset,
                start_date=start_date,
                end_date=end_date,
                refresh=refresh,
                refresh_cookies=refresh_cookies,
                agent_fallback=agent_fallback,
                backfill_historical=backfill_historical,
                limit=limit,
            )
        except Exception as e:
            logger.exception("get_nse_browser_data failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def ingest_nse_repository() -> str:
        """
        Sync git-tracked data/nse parquet into hub without browser fetch.

        Use after cloning the repo or when data/nse/*.parquet was updated locally.
        """
        try:
            _ensure_trade_stack_import()
            from trade_integrations.tools.nse_browser_tools import query_ingest_nse_repository as _ingest

            return _ingest()
        except Exception as e:
            logger.exception("ingest_nse_repository failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def run_nse_browser_mission(
        mission: str = "fii_dii_history",
        refresh_cookies: bool = False,
        agent_fallback: bool = False,
        backfill_historical: bool = False,
    ) -> str:
        """
        Low-level: run one NSE/NSDL browser mission by id (ops/debug).

        Prefer get_nse_browser_data for agent use — it returns parsed rows and handles cache freshness.

        Args:
            mission: fii_dii_history | fpi_nsdl | market_archives
            refresh_cookies: Bootstrap nodriver session cookies before fetch
            agent_fallback: MiniMax operator when deterministic navigation fails

        Returns:
            JSON mission result with status, rows, artifacts, and date_range.
        """
        try:
            _ensure_trade_stack_import()
            from trade_integrations.tools.nse_browser_tools import fetch_nse_browser_data

            return fetch_nse_browser_data(
                mission,
                refresh=True,
                refresh_cookies=refresh_cookies,
                agent_fallback=agent_fallback,
                backfill_historical=backfill_historical,
            )
        except Exception as e:
            logger.exception("run_nse_browser_mission failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def get_hub_news(
        ticker: str = "NIFTY",
        limit: int = 20,
        refresh: bool = False,
    ) -> str:
        """
        Latest verified news for a ticker via the hub gateway (news SSOT).

        Reads hub cache first; pass refresh=True to trigger a live fetch (RSS,
        SearXNG, Moneycontrol, etc.) across all configured sources before reading.

        Args:
            ticker: Ticker or index, e.g. NIFTY, RELIANCE
            limit: Max headlines to return
            refresh: Fetch fresh news before reading

        Returns:
            JSON HubResult: status, data (headlines), source, as_of.
        """
        try:
            _ensure_trade_stack_import()
            from trade_integrations.tools.hub_gateway_tools import query_hub_news as _get

            return _get(ticker, limit=limit, refresh=refresh)
        except Exception as e:
            logger.exception("get_hub_news failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def get_hub_fii_dii(
        start_date: str | None = None,
        end_date: str | None = None,
        refresh: bool = False,
        limit: int = 500,
    ) -> str:
        """
        Daily FII/DII (foreign/domestic institutional investor) net cash flows via the hub gateway.

        Reads hub cache first; pass refresh=True to force a live NSE/NSDL fetch.

        Args:
            start_date: YYYY-MM-DD (default ~30 days ago)
            end_date: YYYY-MM-DD (default today)
            refresh: Force live fetch even if hub cache is fresh
            limit: Max rows returned

        Returns:
            JSON HubResult: status, data (daily FII/DII rows), source, as_of.
        """
        try:
            _ensure_trade_stack_import()
            from trade_integrations.tools.hub_gateway_tools import query_hub_fii_dii as _get

            return _get(start_date, end_date, refresh=refresh, limit=limit)
        except Exception as e:
            logger.exception("get_hub_fii_dii failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def get_hub_index_history(
        index: str = "NIFTY",
        start_date: str | None = None,
        end_date: str | None = None,
        refresh: bool = False,
    ) -> str:
        """
        Daily index OHLCV (open/high/low/close) timeline via the hub gateway.

        Args:
            index: Index symbol — NIFTY or SENSEX
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
            refresh: Force a live refresh via data_router before reading

        Returns:
            JSON HubResult: status, data (daily OHLCV rows), source, as_of.
        """
        try:
            _ensure_trade_stack_import()
            from trade_integrations.tools.hub_gateway_tools import query_hub_index_history as _get

            return _get(index, start_date, end_date, refresh=refresh)
        except Exception as e:
            logger.exception("get_hub_index_history failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def run_browser_task(
        goal: str,
        start_urls: str | None = None,
        output_schema: str | None = None,
        max_steps: int = 50,
        persist: bool = True,
    ) -> str:
        """
        Agentic web browse/extract via local nodriver + MiniMax (ad-hoc research).

        Use for events, filings, macro pages, or any public URL. For preset NSE/NSDL
        datasets (FII/DII, FPI, archives) prefer get_nse_browser_data.

        Args:
            goal: Natural-language objective (required)
            start_urls: JSON array of entry URLs, e.g. ["https://www.rbi.org.in/"]
            output_schema: JSON schema string for structured extraction
            max_steps: MiniMax operator step budget (1–20)
            persist: Save artifacts under reports/hub/_data/nse_browser/tasks/

        Returns:
            JSON with status, structured_output, task_id, hub_path, action_log.
        """
        try:
            from trade_integrations.tools.nse_browser_tools import query_run_browser_task as _run

            urls: list[str] | None = None
            if start_urls:
                parsed = json.loads(start_urls)
                if isinstance(parsed, list):
                    urls = [str(u) for u in parsed]
                elif isinstance(parsed, str):
                    urls = [parsed]
            schema: dict | None = None
            if output_schema:
                schema = json.loads(output_schema)
            return _run(goal, start_urls=urls, output_schema=schema, max_steps=max_steps, persist=persist)
        except Exception as e:
            logger.exception("run_browser_task failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)


    @mcp.tool()
    def get_stock_trade_widget(
        ticker: str,
        refresh: bool = False,
        lookahead_days: int = 14,
    ) -> str:
        """
        Build a structured stock trade-plan widget for Vibe chat.

        Returns JSON with type ``trade_plan.widget`` (asset_type stock) including
        scenarios, charges, ranked approaches, and execute steps.

        Args:
            ticker: NSE equity symbol (RELIANCE, TCS, …)
            refresh: Regenerate hub plan before building widget
            lookahead_days: Event lookahead window

        Returns:
            JSON widget payload (persisted under ~/.vibe-trading/trade_widgets/).
        """
        try:
            from trade_integrations.dataflows.stock_research.widget_payload import (
                build_stock_trade_widget,
            )

            widget = build_stock_trade_widget(
                ticker,
                lookahead_days=lookahead_days,
                refresh=refresh,
            )
            widget_id = widget.get("widget_id")
            if widget_id:
                store = _trade_widget_store_dir() / f"{widget_id}.json"
                store.write_text(json.dumps(widget, indent=2, default=str), encoding="utf-8")
            return json.dumps(widget, indent=2, default=str)
        except Exception as e:
            logger.exception("get_stock_trade_widget failed: %s", e)
            return json.dumps(
                {"type": "trade_plan.widget", "error": str(e), "underlying": ticker, "asset_type": "stock"},
                indent=2,
            )


    def _import_agent_debate():
        _ensure_trade_stack_import()
        import trade_integrations  # noqa: F401
        from trade_integrations.bridge.agent_debate import run_agent_debate
        from trade_integrations.context.hub import (
            is_agent_debate_cache_fresh,
            load_agent_debate_json,
        )

        return run_agent_debate, load_agent_debate_json, is_agent_debate_cache_fresh


    @mcp.tool()
    def run_tradingagents_analysis(
        ticker: str,
        asset_type: str = "stock",
        refresh: bool = False,
    ) -> str:
        """
        Run the TradingAgents multi-agent debate (bull/bear/risk) and save to hub.

        Use when the user finalizes a plan or asks for a second opinion from agents.
        Returns markdown summary; full JSON lives at reports/hub/{TICKER}/agent_debate/.

        Args:
            ticker: Symbol (NIFTY, RELIANCE, …)
            asset_type: stock or options context for prefetch
            refresh: Bypass cached debate when true

        Returns:
            Markdown debate summary with rating and key perspectives.
        """
        try:
            run_agent_debate, load_agent_debate_json, is_agent_debate_cache_fresh = _import_agent_debate()
            from trade_integrations.bridge.hub_context import infer_debate_asset_type

            key = ticker.strip().upper()
            resolved_asset = infer_debate_asset_type(key, asset_type if asset_type in ("options", "stock") else None)
            if not refresh:
                cached = load_agent_debate_json(key)
                if cached and is_agent_debate_cache_fresh(key):
                    from trade_integrations.dataflows.agent_debate.format import format_agent_debate_report

                    return format_agent_debate_report(cached)

            import threading

            if not refresh:
                stale = load_agent_debate_json(key)
                if stale:
                    from trade_integrations.dataflows.agent_debate.format import format_agent_debate_report

                    body = format_agent_debate_report(stale)
                    body += (
                        f"\n\n---\n*Note: debate cache is stale; a fresh run was started in the "
                        f"background for {key}. Check the Vibe Research panel → Agent debate tab.*"
                    )
                else:
                    body = (
                        f"TradingAgents debate started for **{key}** in the background "
                        f"(typically 2–5 minutes).\n\n"
                        f"Results will be saved to `reports/hub/{key}/agent_debate/` and appear in "
                        f"the Vibe **Research → Agent debate** side panel.\n\n"
                        f"Call this tool again with `refresh=false` once complete to read the summary."
                    )
            else:
                body = (
                    f"Refreshing TradingAgents debate for **{key}** in the background.\n"
                    f"Call again with `refresh=false` when the Research panel shows ready."
                )

            def _worker() -> None:
                try:
                    run_agent_debate(key, asset_type=resolved_asset)
                except Exception:
                    logger.exception("_worker failed")
                    pass

            threading.Thread(target=_worker, daemon=True, name=f"mcp-debate-{key}").start()
            return body
        except Exception as e:
            logger.exception("_worker failed: %s", e)
            return f"Error running TradingAgents analysis: {str(e)}"


    def _import_quant_review():
        _ensure_trade_stack_import()
        import trade_integrations  # noqa: F401
        from trade_integrations.bridge.quant_review import run_quant_review
        from trade_integrations.context.hub import (
            is_quant_review_cache_fresh,
            load_quant_review_json,
        )

        return run_quant_review, load_quant_review_json, is_quant_review_cache_fresh


    @mcp.tool()
    def run_quant_review(
        ticker: str = "NIFTY",
        horizon_days: int = 14,
        refresh: bool = False,
    ) -> str:
        """
        Run India Quant Reviewer — second opinion vs Ridge forecast (TA + flows + surprises).

        Saves to reports/hub/{TICKER}/quant_review/latest.json. Label as reviewer opinion,
        not the headline model forecast.

        Args:
            ticker: Index symbol (NIFTY, BANKNIFTY)
            horizon_days: Prediction horizon for profile selection
            refresh: Recompute even when cache is fresh

        Returns:
            JSON summary with surprises, disagreements, and TA consensus.
        """
        import json

        try:
            run_review, load_review, is_fresh = _import_quant_review()
            key = ticker.strip().upper()
            if not refresh:
                cached = load_review(key)
                if cached and is_fresh(key):
                    return json.dumps(cached, indent=2, default=str)
            payload = run_review(key, horizon_days=horizon_days, save=True)
            return json.dumps(payload, indent=2, default=str)
        except Exception as e:
            logger.exception("run_quant_review failed: %s", e)
            return json.dumps({"error": str(e), "ticker": ticker}, indent=2)


    @mcp.tool()
    def get_stock_browse(ticker: str) -> str:
        """
        Compact in-chat browse for an equity (price, sector, 52w range, peers).

        Args:
            ticker: NSE equity symbol (e.g. RELIANCE, TCS)

        Returns:
            JSON with browse_summary and markdown table for chat.
        """
        try:
            (
                build_stock_browse_summary,
                format_stock_browse_markdown,
                _,
                _,
                load_stock_research_json,
                _,
            ) = _import_stock_research()
            from trade_integrations.context.hub import load_company_research_json
            from trade_integrations.dataflows.market_quotes import fetch_live_quote

            sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
            doc = load_stock_research_json(sym)
            if doc and doc.browse_summary:
                summary = doc.browse_summary
            else:
                company = load_company_research_json(sym)
                identity = company.identity if company else {}
                quote = fetch_live_quote(sym)
                peers = company.peers if company else []
                summary = build_stock_browse_summary(
                    ticker=sym,
                    identity=identity,
                    quote=quote,
                    peers=peers,
                )
            return json.dumps(
                {"browse_summary": summary, "markdown": format_stock_browse_markdown(summary)},
                indent=2,
                default=str,
            )
        except Exception as e:
            logger.exception("get_stock_browse failed: %s", e)
            return f"Error browsing stock: {str(e)}"


    @mcp.tool()
    def get_stock_trade_plan(ticker: str, refresh: bool = False, lookahead_days: int = 14) -> str:
        """
        Load or generate a stock trade plan from the trade-stack hub.

        Includes prediction, ranked approaches, recommended action, charges, and steps.

        Args:
            ticker: Equity symbol (RELIANCE, TCS, …)
            refresh: Regenerate even if cache exists
            lookahead_days: Event lookahead window

        Returns:
            Markdown stock trade plan.
        """
        try:
            _ensure_trade_stack_import()
            from trade_integrations.tools.stock_research_tools import fetch_stock_research_report

            return fetch_stock_research_report(
                ticker,
                lookahead_days=lookahead_days,
                use_cache=not refresh,
            )
        except Exception as e:
            logger.exception("get_stock_trade_plan failed: %s", e)
            return f"Error loading stock trade plan: {str(e)}"


    @mcp.tool()
    def get_index_trade_plan(
        ticker: str = "NIFTY",
        refresh: bool = False,
        horizon_days: int | None = None,
    ) -> str:
        """
        Load or generate an index trade plan from the trade-stack hub.

        Includes prediction range, constituent attribution, macro factors, regime,
        scenarios, and model accuracy metrics. Set refresh=true to bypass cache.

        Args:
            ticker: Index symbol (NIFTY, BANKNIFTY, …)
            refresh: Regenerate even if cache exists
            horizon_days: Prediction horizon in days (default from env, usually 14)

        Returns:
            JSON with index_research payload and markdown summary.
        """
        try:
            (
                _,
                format_index_report,
                load_index_research_json,
                _,
                fetch_index_research_report,
            ) = _import_index_research()
            sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
            if refresh:
                markdown = fetch_index_research_report(
                    sym,
                    horizon_days=horizon_days,
                    use_cache=False,
                )
                doc = load_index_research_json(sym)
            else:
                doc = load_index_research_json(sym)
                if doc:
                    markdown = format_index_report(doc)
                else:
                    markdown = fetch_index_research_report(sym, horizon_days=horizon_days)
                    doc = load_index_research_json(sym)
            payload = doc.to_dict() if doc and hasattr(doc, "to_dict") else None
            if payload is None and doc is not None:
                from dataclasses import asdict

                payload = asdict(doc)
                payload["as_of"] = doc.as_of.isoformat()
            return json.dumps(
                {"index_research": payload, "markdown": markdown},
                indent=2,
                default=str,
            )
        except Exception as e:
            logger.exception("get_index_trade_plan failed: %s", e)
            return f"Error loading index trade plan: {str(e)}"


    @mcp.tool()
    def get_index_trade_widget(
        ticker: str = "NIFTY",
        refresh: bool = False,
        horizon_days: int | None = None,
    ) -> str:
        """
        Build a structured index trade-plan widget for Vibe chat.

        Includes prediction range, SHAP/marginal factor contributions, sensitivity
        curves (index vs factor shocks), and event-impact paths.

        Args:
            ticker: Index symbol (NIFTY, BANKNIFTY, …)
            refresh: Regenerate hub research before building widget
            horizon_days: Prediction horizon in days (default 14)

        Returns:
            JSON widget payload (type trade_plan.widget, asset_type index).
        """
        try:
            from trade_integrations.dataflows.index_research.widget_payload import (
                build_index_trade_widget,
            )

            widget = build_index_trade_widget(
                ticker,
                horizon_days=horizon_days,
                refresh=refresh,
                widget_intent="index_outlook",
            )
            widget_id = widget.get("widget_id")
            if widget_id:
                store = _trade_widget_store_dir() / f"{widget_id}.json"
                store.write_text(json.dumps(widget, indent=2, default=str), encoding="utf-8")
            return json.dumps(widget, indent=2, default=str)
        except Exception as e:
            logger.exception("get_index_trade_widget failed: %s", e)
            return f"Error building index trade widget: {str(e)}"


    @mcp.tool()
    def get_pipeline_snapshot(ticker: str = "NIFTY", pipeline_as_of: str = "") -> str:
        """Summarize the bound Analysis pipeline snapshot (spot, prediction, contributors)."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_get_pipeline_snapshot,
            )

            return tool_get_pipeline_snapshot(ticker, pipeline_as_of)
        except Exception as e:
            logger.exception("get_pipeline_snapshot failed: %s", e)
            return f"Error: {e}"


    @mcp.tool()
    def query_factor_explanation(ticker: str = "NIFTY", pipeline_as_of: str = "", limit: int = 8) -> str:
        """Top macro factor contributors from the bound pipeline snapshot."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_query_factor_explanation,
            )

            return tool_query_factor_explanation(ticker, pipeline_as_of, limit=limit)
        except Exception as e:
            logger.exception("query_factor_explanation failed: %s", e)
            return f"Error: {e}"


    @mcp.tool()
    def query_factor_sensitivity(ticker: str = "NIFTY", pipeline_as_of: str = "", limit: int = 8) -> str:
        """Factor sensitivity curves from the bound pipeline snapshot."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_query_factor_sensitivity,
            )

            return tool_query_factor_sensitivity(ticker, pipeline_as_of, limit=limit)
        except Exception as e:
            logger.exception("query_factor_sensitivity failed: %s", e)
            return f"Error: {e}"


    @mcp.tool()
    def query_equation_coefficients(ticker: str = "NIFTY", pipeline_as_of: str = "") -> str:
        """Ridge equation coefficients from the bound pipeline snapshot."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_query_equation_coefficients,
            )

            return tool_query_equation_coefficients(ticker, pipeline_as_of)
        except Exception as e:
            logger.exception("query_equation_coefficients failed: %s", e)
            return f"Error: {e}"


    @mcp.tool()
    def query_constituent_drivers(ticker: str = "NIFTY", pipeline_as_of: str = "", limit: int = 10) -> str:
        """Constituent drivers from the bound pipeline snapshot."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_query_constituent_drivers,
            )

            return tool_query_constituent_drivers(ticker, pipeline_as_of, limit=limit)
        except Exception as e:
            logger.exception("query_constituent_drivers failed: %s", e)
            return f"Error: {e}"


    @mcp.tool()
    def get_pipeline_news_items(
        ticker: str = "NIFTY",
        pipeline_as_of: str = "",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 20,
    ) -> str:
        """Verified headlines embedded in the pipeline snapshot (optional date filter)."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_get_pipeline_news_items,
            )

            return tool_get_pipeline_news_items(
                ticker, pipeline_as_of, start_date=start_date, end_date=end_date, limit=limit
            )
        except Exception as e:
            logger.exception("get_pipeline_news_items failed: %s", e)
            return f"Error: {e}"


    @mcp.tool()
    def get_live_news_impact(ticker: str = "NIFTY", pipeline_as_of: str = "", limit: int = 12) -> str:
        """Live news impact for `ticker` (ranked top factors + recent headlines), queried
        directly from the hub — unlike get_pipeline_news_items, reflects headlines ingested
        after this session's Analysis snapshot was taken."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_get_live_news_impact,
            )

            return tool_get_live_news_impact(ticker, pipeline_as_of, limit=limit)
        except Exception as e:
            logger.exception("get_live_news_impact failed: %s", e)
            return f"Error: {e}"


    @mcp.tool()
    def get_playground_context(ticker: str = "NIFTY", pipeline_as_of: str = "") -> str:
        """Playground factor/headline bundle from the bound pipeline snapshot."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_get_playground_context,
            )

            return tool_get_playground_context(ticker, pipeline_as_of)
        except Exception as e:
            logger.exception("get_playground_context failed: %s", e)
            return f"Error: {e}"


    @mcp.tool()
    def simulate_pipeline_scenario(
        ticker: str = "NIFTY",
        pipeline_as_of: str = "",
        primary_factor: str | None = None,
        primary_shock_pct: float | None = None,
        horizon_days: int | None = None,
        factor_overrides_json: str = "{}",
    ) -> str:
        """Single-factor what-if on the bound pipeline snapshot."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_simulate_pipeline_scenario,
            )

            factor_overrides = None
            if factor_overrides_json and factor_overrides_json.strip() not in ("", "{}"):
                factor_overrides = json.loads(factor_overrides_json)
            return tool_simulate_pipeline_scenario(
                ticker,
                pipeline_as_of,
                factor_overrides=factor_overrides,
                primary_factor=primary_factor,
                primary_shock_pct=primary_shock_pct,
                horizon_days=horizon_days,
            )
        except Exception as e:
            logger.exception("simulate_pipeline_scenario failed: %s", e)
            return f"Error: {e}"


    @mcp.tool()
    def save_news_scenario_draft(
        ticker: str = "NIFTY",
        pipeline_as_of: str = "",
        draft_json: str = "{}",
    ) -> str:
        """Save a news scenario draft (event + outcomes) before quant run."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_save_news_scenario_draft,
            )

            return tool_save_news_scenario_draft(ticker, pipeline_as_of, draft_json)
        except Exception as e:
            logger.exception("save_news_scenario_draft failed: %s", e)
            return f"Error: {e}"


    @mcp.tool()
    def run_news_event_scenario(
        ticker: str = "NIFTY",
        pipeline_as_of: str = "",
        draft_id: str = "",
        session_id: str | None = None,
    ) -> str:
        """Run quant paths for all outcomes in a saved draft."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_run_news_event_scenario,
            )

            return tool_run_news_event_scenario(
                ticker, pipeline_as_of, draft_id, session_id=session_id
            )
        except Exception as e:
            logger.exception("run_news_event_scenario failed: %s", e)
            return f"Error: {e}"


    @mcp.tool()
    def get_news_scenario_widget(
        ticker: str = "NIFTY",
        pipeline_as_of: str = "",
        scenario_id: str = "",
        selected_outcome_id: str | None = None,
    ) -> str:
        """Build a news_event_scenario trade_plan.widget from a saved scenario."""
        try:
            from trade_integrations.dataflows.news_hub_bridge.internal.news_scenario_tools import (
                tool_get_news_scenario_widget,
            )

            return tool_get_news_scenario_widget(
                ticker, pipeline_as_of, scenario_id, selected_outcome_id=selected_outcome_id
            )
        except Exception as e:
            logger.exception("get_news_scenario_widget failed: %s", e)
            return f"Error: {e}"

    # Tool to get authoritative market context
    @mcp.tool()
    def market_context() -> str:
        """
        Get authoritative OpenAlgo market context (broker, analyze mode, simulator).

        Returns:
            JSON with market context including context_generation, data_broker,
            execution_venue, analyze_mode, market_region, positions_authority,
            and simulator replay state when applicable.
        """
        try:
            from services.marketcontext_service import get_marketcontext

            if not api_key:
                return json.dumps({"status": "error", "error": "API key not configured"}, indent=2)
            _success, response_data, _code = get_marketcontext(api_key=api_key)
            return json.dumps(response_data, indent=2, default=str)
        except Exception as e:
            logger.exception("market_context failed: %s", e)
            return json.dumps({"status": "error", "error": str(e)}, indent=2)
