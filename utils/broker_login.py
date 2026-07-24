"""Centralized broker login / connect URL construction."""

from __future__ import annotations

import os
import secrets
from urllib.parse import quote

from utils.broker_registry import AUTH_FLOW_MAP, get_broker_descriptor
from utils.logging import get_logger

logger = get_logger(__name__)

FYERS_OAUTH_STATE = "2e9b44629ebb28226224d09db3ffb47c"


def _host_base() -> str:
    host = (os.getenv("HOST_SERVER") or "").strip().rstrip("/")
    if host:
        return host
    redirect_url = (os.getenv("REDIRECT_URL") or "").strip()
    if redirect_url:
        from urllib.parse import urlparse

        parsed = urlparse(redirect_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    port = (os.getenv("FLASK_PORT") or "5000").strip()
    host_ip = (os.getenv("FLASK_HOST_IP") or "127.0.0.1").strip()
    return f"http://{host_ip}:{port}"


def build_redirect_url_for_broker(broker_id: str, host_base: str | None = None) -> str:
    base = (host_base or _host_base()).rstrip("/")
    return f"{base}/{broker_id.strip().lower()}/callback"


def _flattrade_api_key(full_key: str) -> str:
    if not full_key:
        return ""
    parts = full_key.split(":::")
    return parts[1] if len(parts) > 1 else full_key


def _resolve_credentials(broker_id: str) -> tuple[str, str]:
    from utils.broker_credentials import apply_broker_credentials

    return apply_broker_credentials(broker_id)


def build_connect_url(
    broker_id: str,
    *,
    host_base: str | None = None,
    redirect_url: str | None = None,
    broker_api_key: str | None = None,
    pocketful_state: str | None = None,
) -> str:
    """Return the URL the browser should navigate to when connecting a broker."""
    broker_id = broker_id.strip().lower()
    descriptor = get_broker_descriptor(broker_id)
    auth_flow = descriptor.auth_flow if descriptor else AUTH_FLOW_MAP.get(broker_id, "callback")

    host = (host_base or _host_base()).rstrip("/")
    redirect = redirect_url or build_redirect_url_for_broker(broker_id, host)

    if broker_api_key is None:
        broker_api_key, _ = _resolve_credentials(broker_id)
    else:
        _resolve_credentials(broker_id)

    if auth_flow == "totp":
        if broker_id == "samco":
            return f"{host}/broker/samco/auth"
        return f"{host}/broker/{broker_id}/totp"

    if auth_flow == "oauth_init":
        if broker_id == "dhan":
            return f"{host}/dhan/initiate-oauth"
        raise ValueError(f"oauth_init not configured for broker '{broker_id}'")

    if auth_flow == "oauth_external":
        if broker_id == "compositedge":
            return (
                "https://xts.compositedge.com/interactive/thirdparty"
                f"?appKey={broker_api_key}&returnURL={quote(redirect, safe='')}"
            )
        if broker_id == "flattrade":
            flattrade_key = _flattrade_api_key(broker_api_key)
            return f"https://auth.flattrade.in/?app_key={flattrade_key}"
        if broker_id == "fyers":
            return (
                "https://api-t1.fyers.in/api/v3/generate-authcode"
                f"?client_id={broker_api_key}&redirect_uri={quote(redirect, safe='')}"
                f"&response_type=code&state={FYERS_OAUTH_STATE}"
            )
        if broker_id == "upstox":
            return (
                "https://api.upstox.com/v2/login/authorization/dialog"
                f"?response_type=code&client_id={broker_api_key}"
                f"&redirect_uri={quote(redirect, safe='')}"
            )
        if broker_id == "zerodha":
            return f"https://kite.trade/connect/login?api_key={broker_api_key}"
        if broker_id == "arrow":
            return f"https://app.arrow.trade/app/login?appID={broker_api_key}"
        if broker_id == "paytm":
            return (
                f"https://login.paytmmoney.com/merchant-login?apiKey={broker_api_key}&state={{default}}"
            )
        if broker_id == "pocketful":
            state = pocketful_state or secrets.token_urlsafe(16)
            scope = quote("orders holdings")
            return (
                "https://trade.pocketful.in/oauth2/auth"
                f"?client_id={broker_api_key}&redirect_uri={quote(redirect, safe='')}"
                f"&response_type=code&scope={scope}&state={quote(state)}"
            )
        if broker_id == "aliceblue":
            return f"https://ant.aliceblueonline.com/?appcode={broker_api_key}"
        if broker_id == "shoonya":
            client_id = broker_api_key.split(":::")[1] if ":::" in broker_api_key else broker_api_key
            return (
                "https://api.shoonya.com/OAuthlogin/authorize/oauth"
                f"?client_id={client_id}"
            )
        if broker_id == "zebu":
            client_id = broker_api_key.split(":::")[1] if ":::" in broker_api_key else broker_api_key
            return f"https://go.mynt.in/OAuthlogin/authorize/oauth?client_id={client_id}"
        if broker_id == "tradesmart":
            client_id = broker_api_key.split(":::")[1] if ":::" in broker_api_key else broker_api_key
            return (
                "https://v2api.tradesmartonline.in/OAuthlogin/authorize/oauth"
                f"?client_id={client_id}"
            )
        if broker_id == "rmoney":
            return f"{host}/rmoney/callback"
        logger.warning("oauth_external fallback to callback for broker %s", broker_id)

    if broker_id == "iiflcapital":
        return f"{host}/iiflcapital/callback"

    return f"{host}/{broker_id}/callback"
