"""History service validation for Alpaca US symbols."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services import history_service as hs


@pytest.mark.unit
def test_validate_symbol_exchange_skips_token_for_alpaca() -> None:
    ok, err = hs.validate_symbol_exchange("AAPL", "NASDAQ", broker="alpaca")
    assert ok is True
    assert err is None


@pytest.mark.unit
def test_validate_symbol_exchange_requires_token_for_india_broker() -> None:
    with patch("services.history_service.get_token", return_value=None):
        ok, err = hs.validate_symbol_exchange("NOTATOKEN", "NSE", broker="zerodha")
    assert ok is False
    assert err is not None
