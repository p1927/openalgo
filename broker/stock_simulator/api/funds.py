"""Funds stub — sandbox/analyzer supplies balances during replay."""

from __future__ import annotations


def test_auth_token(auth_token):  # noqa: ANN001
    if auth_token:
        return True, None
    return False, "Missing simulator auth token"


def get_margin_data(auth_token):  # noqa: ANN001
    return {
        "availablecash": 1000000.0,
        "collateral": 0.0,
        "m2mrealized": 0.0,
        "m2munrealized": 0.0,
        "utiliseddebits": 0.0,
    }
