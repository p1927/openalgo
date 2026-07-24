"""Single authority for broker discovery, metadata, and availability."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from utils.logging import get_logger

logger = get_logger(__name__)

# Fallback auth flows until all plugin.json files carry auth_flow.
AUTH_FLOW_MAP: dict[str, str] = {
    "fivepaisa": "totp",
    "fivepaisaxts": "callback",
    "aliceblue": "oauth_external",
    "angel": "totp",
    "arrow": "oauth_external",
    "compositedge": "oauth_external",
    "dhan": "oauth_init",
    "deltaexchange": "callback",
    "indmoney": "callback",
    "dhan_sandbox": "callback",
    "stock_simulator": "callback",
    "definedge": "totp",
    "firstock": "totp",
    "flattrade": "oauth_external",
    "motilal": "totp",
    "fyers": "oauth_external",
    "groww": "callback",
    "ibulls": "callback",
    "iifl": "callback",
    "iiflcapital": "callback",
    "jainamxts": "callback",
    "kotak": "totp",
    "mstock": "totp",
    "nubra": "totp",
    "paytm": "oauth_external",
    "pocketful": "oauth_external",
    "rmoney": "oauth_external",
    "samco": "totp",
    "shoonya": "oauth_external",
    "tradejini": "totp",
    "tradesmart": "oauth_external",
    "upstox": "oauth_external",
    "wisdom": "callback",
    "zebu": "oauth_external",
    "zerodha": "oauth_external",
    "alpaca": "api_key_env",
}

LOGIN_NOTICE_MAP: dict[str, str] = {
    "zerodha": (
        "Zerodha requires an active Kite Connect data subscription for market data access."
    ),
    "dhan": "Dhan requires an active Data API subscription for market data access.",
}

DISPLAY_NAME_MAP: dict[str, str] = {
    "fivepaisa": "5 Paisa",
    "fivepaisaxts": "5 Paisa (XTS)",
    "aliceblue": "Alice Blue",
    "angel": "Angel One",
    "compositedge": "CompositEdge",
    "dhan_sandbox": "Dhan (Sandbox)",
    "stock_simulator": "NSE Simulator (Replay)",
    "deltaexchange": "Delta Exchange",
    "iiflcapital": "IIFL Capital",
    "jainamxts": "JainamXts",
    "kotak": "Kotak Securities",
    "mstock": "mStock by Mirae Asset",
    "motilal": "Motilal Oswal",
    "paytm": "Paytm Money",
    "rmoney": "RMoney",
    "tradesmart": "TradeSmart",
    "wisdom": "Wisdom Capital",
    "alpaca": "Alpaca",
}


def _openalgo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_broker_from_redirect_url(redirect_url: str | None) -> str:
    """Extract broker id from REDIRECT_URL (``.../<broker>/callback``)."""
    if not redirect_url:
        return ""
    try:
        match = re.search(r"/([^/]+)/callback$", redirect_url.strip())
        if match:
            return match.group(1).lower()
    except Exception:
        pass
    return ""


def parse_valid_brokers_env(valid_brokers_str: str | None = None) -> list[str]:
    raw = valid_brokers_str if valid_brokers_str is not None else os.getenv("VALID_BROKERS", "")
    return sorted({b.strip().lower() for b in raw.split(",") if b.strip()})


def discover_plugin_brokers(broker_directory: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Scan broker/*/plugin.json and return raw plugin metadata keyed by broker id."""
    root = Path(broker_directory) if broker_directory else _openalgo_root() / "broker"
    plugins: dict[str, dict[str, Any]] = {}

    if not root.is_dir():
        return plugins

    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name == "__pycache__":
            continue
        plugin_file = entry / "plugin.json"
        if not plugin_file.is_file():
            continue
        try:
            with plugin_file.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                plugins[entry.name.lower()] = data
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Error reading plugin.json for %s: %s", entry.name, exc)

    return plugins


def get_default_broker() -> str:
    return get_broker_from_redirect_url(os.getenv("REDIRECT_URL", ""))


def resolve_default_broker_for_list(broker_ids: Iterable[str]) -> str | None:
    """Return REDIRECT_URL broker when in the available set, else first sorted id."""
    ids = {str(broker_id).strip().lower() for broker_id in broker_ids if str(broker_id).strip()}
    if not ids:
        return None
    default = get_default_broker()
    if default and default in ids:
        return default
    return sorted(ids)[0]


def _resolve_display_name(broker_id: str, plugin: dict[str, Any]) -> str:
    if plugin.get("display_name"):
        return str(plugin["display_name"]).strip()
    if broker_id in DISPLAY_NAME_MAP:
        return DISPLAY_NAME_MAP[broker_id]
    desc = str(plugin.get("Description") or plugin.get("description") or "").strip()
    if desc:
        return desc.split("—")[0].split(" - ")[0].strip() or desc
    plugin_name = str(plugin.get("Plugin Name") or plugin.get("broker_name") or "").strip()
    if plugin_name and plugin_name.lower() != broker_id:
        return plugin_name.replace("_", " ").title()
    return broker_id.replace("_", " ").title()


def _resolve_auth_flow(broker_id: str, plugin: dict[str, Any]) -> str:
    flow = str(plugin.get("auth_flow") or plugin.get("auth_type") or "").strip().lower()
    if flow:
        return flow
    return AUTH_FLOW_MAP.get(broker_id, "callback")


def _credentials_configured(broker_id: str) -> bool:
    from utils.broker_credentials import resolve_broker_credentials

    api_key, api_secret = resolve_broker_credentials(broker_id)
    if broker_id == "stock_simulator":
        return True
    return bool(api_key or api_secret)


@dataclass
class BrokerDescriptor:
    id: str
    display_name: str
    description: str
    broker_type: str
    auth_flow: str
    supported_exchanges: list[str] = field(default_factory=list)
    is_default: bool = False
    credentials_configured: bool = False
    connect_url: str | None = None
    login_notice: str | None = None
    requires_app_restart: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_broker_descriptor(
    broker_id: str,
    *,
    default_broker: str | None = None,
    include_connect_url: bool = False,
) -> BrokerDescriptor | None:
    broker_id = broker_id.strip().lower()
    plugins = discover_plugin_brokers()
    if broker_id not in plugins:
        return None

    plugin = plugins[broker_id]
    default_broker = (default_broker if default_broker is not None else get_default_broker()).lower()
    auth_flow = _resolve_auth_flow(broker_id, plugin)
    login_notice = plugin.get("login_notice") or LOGIN_NOTICE_MAP.get(broker_id)
    if login_notice:
        login_notice = str(login_notice).strip() or None

    descriptor = BrokerDescriptor(
        id=broker_id,
        display_name=_resolve_display_name(broker_id, plugin),
        description=str(plugin.get("Description") or plugin.get("description") or "").strip(),
        broker_type=str(plugin.get("broker_type") or "IN_stock"),
        auth_flow=auth_flow,
        supported_exchanges=list(plugin.get("supported_exchanges") or []),
        is_default=broker_id == default_broker,
        credentials_configured=_credentials_configured(broker_id),
        login_notice=login_notice,
        requires_app_restart=False,
    )

    if include_connect_url:
        from utils.broker_login import build_connect_url

        descriptor.connect_url = build_connect_url(broker_id)

    return descriptor


def list_available_brokers(
    *,
    valid_brokers: list[str] | None = None,
    default_broker: str | None = None,
    include_connect_url: bool = False,
) -> list[BrokerDescriptor]:
    """Return VALID_BROKERS ∩ installed plugin brokers, sorted by display name."""
    allowed = set(valid_brokers if valid_brokers is not None else parse_valid_brokers_env())
    plugins = discover_plugin_brokers()
    default_broker = (default_broker if default_broker is not None else get_default_broker()).lower()

    missing_plugins = sorted(allowed - set(plugins.keys()))
    if missing_plugins:
        logger.warning(
            "VALID_BROKERS entries missing plugin.json: %s",
            ", ".join(missing_plugins),
        )

    descriptors: list[BrokerDescriptor] = []
    for broker_id in sorted(allowed):
        if broker_id not in plugins:
            continue
        desc = get_broker_descriptor(
            broker_id,
            default_broker=default_broker,
            include_connect_url=include_connect_url,
        )
        if desc:
            descriptors.append(desc)

    descriptors.sort(key=lambda item: item.display_name.lower())
    return descriptors


def validate_registry_at_startup() -> list[str]:
    """Return warning messages for registry inconsistencies."""
    warnings: list[str] = []
    allowed = set(parse_valid_brokers_env())
    plugins = set(discover_plugin_brokers().keys())

    for broker_id in sorted(allowed - plugins):
        warnings.append(f"VALID_BROKERS includes '{broker_id}' but broker/{broker_id}/plugin.json is missing")

    default_broker = get_default_broker()
    if default_broker and default_broker not in allowed:
        warnings.append(
            f"REDIRECT_URL broker '{default_broker}' is not listed in VALID_BROKERS"
        )
    if default_broker and default_broker not in plugins:
        warnings.append(
            f"REDIRECT_URL broker '{default_broker}' has no plugin.json"
        )

    return warnings


def default_valid_brokers_sample() -> str:
    """Read VALID_BROKERS default from .sample.env for install scripts."""
    sample_path = _openalgo_root() / ".sample.env"
    if not sample_path.is_file():
        return ""
    for line in sample_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("VALID_BROKERS"):
            _, _, value = stripped.partition("=")
            return value.strip().strip("'\"")
    return ""
