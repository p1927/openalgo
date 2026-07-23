"""Minimal httpx helpers for Alpaca REST (no trade_integrations import)."""

from __future__ import annotations

import json
import os
from typing import Any

from utils.httpx_client import get_httpx_client
from utils.logging import get_logger

logger = get_logger(__name__)


def resolve_api_secret(explicit: str | None = None) -> str:
    if explicit:
        return explicit.strip()
    from utils.broker_credentials import resolve_broker_credentials

    _, secret = resolve_broker_credentials("alpaca")
    if secret:
        return secret
    return (
        (os.getenv("BROKER_API_SECRET") or "")
        or (os.getenv("ALPACA_API_SECRET") or "")
        or (os.getenv("ALPACA_SECRET_KEY") or "")
    ).strip()


def auth_headers(api_key: str, api_secret: str | None = None) -> dict[str, str]:
    secret = resolve_api_secret(api_secret)
    return {
        "APCA-API-KEY-ID": api_key.strip(),
        "APCA-API-SECRET-KEY": secret,
    }


def alpaca_request(
    method: str,
    url: str,
    api_key: str,
    *,
    api_secret: str | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any] | list[Any]]:
    client = get_httpx_client()
    headers = auth_headers(api_key, api_secret)
    try:
        response = client.request(
            method.upper(),
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=timeout,
        )
    except Exception as exc:
        logger.exception("Alpaca request failed: %s", url)
        return 0, {"message": str(exc)}

    try:
        payload = response.json() if response.content else {}
    except json.JSONDecodeError:
        payload = {"message": response.text[:200]}

    if isinstance(payload, list):
        return response.status_code, payload
    return response.status_code, payload if isinstance(payload, dict) else {"data": payload}
