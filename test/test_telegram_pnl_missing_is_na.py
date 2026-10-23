"""A P&L figure the broker omits is shown "n/a", never 0 (D410)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.telegram_bot_service import TelegramBotService


def _fmt(data):
    svc = TelegramBotService.__new__(TelegramBotService)
    return svc._format_pnl_funds({"status": "success", "data": data}, "Rs ")


def test_missing_realized_is_na_and_total_is_na():
    out = _fmt({"m2munrealized": "12.50"})
    assert "Realized P&L*\n└ n/a" in out
    assert "Unrealized P&L*\n└ Rs 12.50" in out
    assert "Total P&L*\n└ n/a" in out


def test_present_zero_stays_zero_and_nan_is_na():
    out = _fmt({"m2mrealized": "0.00", "m2munrealized": "nan"})
    assert "Realized P&L*\n└ Rs 0.00" in out
    assert "Unrealized P&L*\n└ n/a" in out


def test_both_present_sum():
    assert "Total P&L*\n└ Rs 3.00" in _fmt({"m2mrealized": 1, "m2munrealized": "2"})
