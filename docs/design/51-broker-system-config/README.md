# 51 - Broker And System Configuration

## Configuration Sources

OpenAlgo does not use a `config_service.py` or broker configuration database. Runtime settings come from:

| Source | Examples |
|---|---|
| `.env` | Broker keys, redirect/host/WebSocket URLs, database paths, rate limits, security flags |
| `broker/*/plugin.json` | Broker name, type, exchanges, leverage capability |
| Main settings tables | Analyzer mode, SMTP, notification/application preferences |

Environment validation runs before application imports. Import-time settings require a restart after change.

## Broker Credential API

`blueprints/broker_credentials.py` provides session-authenticated `/api/broker/credentials` GET/POST and `/api/broker/capabilities` GET.

GET masks secrets with a fixed-length suffix and returns raw length separately for UI state. POST writes only supplied values to `.env`, validates redirect/host/WebSocket formats and selected broker-specific composite keys, and returns `restart_required: true`.

The UI is part of `frontend/src/pages/Profile.tsx`. Broker selection/login uses `BrokerSelect.tsx` and `BrokerTOTP.tsx`.

## Broker Registry (single authority)

`utils/broker_registry.py` discovers installed brokers from `broker/*/plugin.json`, intersects with `VALID_BROKERS`, and emits `BrokerDescriptor` records (display name, auth flow, login notices).

| Route | Purpose |
|---|---|
| `GET /auth/brokers` | Session-authenticated broker list for login UI |
| `POST /auth/broker/prepare-connect` | Apply credentials + return `connect_url` for selected broker |
| `GET /api/broker/credentials` | Includes `brokers[]` descriptors for Profile UI |

Login connect URLs are built server-side in `utils/broker_login.py` (not in the React bundle).

## Public Broker Config

Use `GET /auth/brokers` (session-authenticated) for login UI broker lists.

## plugin.json fields

| Field | Purpose |
|---|---|
| `display_name` | Human label in login/profile dropdowns |
| `auth_flow` | `callback`, `oauth_external`, `oauth_init`, `totp`, `api_key_env` |
| `login_notice` | Optional subscription/requirements alert on login page |

## Capability Loading

At startup `utils/plugin_loader.py` caches metadata for all 34 plugin directories. `/api/broker/capabilities` resolves the current session broker. A missing capability record falls back to a minimal `IN_stock` object with no exchanges.

## Security Boundaries

- `.env` is installation-secret state and must not be committed.
- Browser reads receive masked credentials; writes never echo submitted secrets.
- Changes are CSRF-protected and session-authenticated.
- Updating `.env` is not hot reload; restart is explicit.
- Login-time broker tokens are encrypted in `database/auth_db.py`, separate from static broker application credentials.

## Key Files

| File | Purpose |
|---|---|
| `.sample.env` | Environment contract and defaults |
| `utils/env_check.py` | Startup validation |
| `utils/broker_registry.py` | Broker discovery + descriptors |
| `utils/broker_login.py` | Connect URL construction |
| `utils/plugin_loader.py` | Plugin/capability discovery |
| `blueprints/broker_credentials.py` | Credential and capability API |
| `blueprints/auth.py` | Broker config/login routes |
| `frontend/src/pages/Profile.tsx` | Configuration UI |
| `broker/*/plugin.json` | Capability metadata |
