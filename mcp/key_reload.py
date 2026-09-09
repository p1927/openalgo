"""Rebind the stdio MCP server's OpenAlgo API key when it is rotated on disk.

Fork-owned sidecar, loaded by path from ``mcp/mcpserver.py`` (same trick and
same reason as ``mcp/custom_tools.py``: ``mcp/`` is not a package and its name
collides with the pip-installed ``mcp`` SDK). Kept out of ``mcpserver.py``
itself so re-syncing with upstream never has to reconcile this against
upstream's own edits to that file -- see ``docs/FORK_CONVENTIONS.md``.

The problem this closes
-----------------------
``mcp/mcpserver.py``'s stdio branch takes its api_key/host from ``sys.argv``
once, at import, and never re-reads them. The argv values come from
``~/.vibe-trading/agent.json`` (rendered by ``scripts/setup_vibe.py`` and
regenerated on every ``trade heal``/``trade up``), which the external MCP client
-- Claude Desktop / Cursor / Windsurf / ``claude mcp`` -- reads only when it
spawns its connection. Rotating ``OPENALGO_API_KEY`` therefore rewrites the file
but leaves an already-running stdio subprocess serving the old, now-invalid key
until a human restarts that client, and none of those clients expose a
scriptable "reload MCP servers" signal.

So the server watches its own config instead: poll ``agent.json``'s mtime, and
when the ``(api_key, host)`` pair baked into the ``openalgo`` server entry's
``args`` differs from what is currently bound, build a brand-new SDK client and
only then swap the module globals, in one step, via ``mcpserver.init_for_http``.

Rules this file exists to enforce (see
``.claude/backlog/items/2026-09-05-openalgo-mcp-agentjson-restart.md``):

* **Build first, swap after.** An in-flight tool call must never observe a
  half-swapped ``(api_key, host, client)`` triple.
* **A failed rebind keeps the old client.** The server is never left keyless.
* **Fail loud.** ``maybe_reload()`` raises; the polling thread catches, logs the
  failure at ``error`` level with a traceback, and keeps the old key bound.
  Silently continuing to serve a stale key is the exact failure mode this
  item was filed to close.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

# Poll cadence for agent.json's mtime. A stat() every 15s is free next to what
# a single MCP tool call costs; override with OPENALGO_MCP_KEY_POLL_SECONDS,
# and set it to 0 to turn the watcher off entirely.
DEFAULT_POLL_SECONDS = 15.0
POLL_ENV_VAR = "OPENALGO_MCP_KEY_POLL_SECONDS"

# Key of this server's entry inside agent.json's "mcpServers" object, as
# rendered from stack/vibe/agent.json.template.
SERVER_KEY = "openalgo"

# args[0]/args[1] of that entry, per the template:
#   "args": ["{{OPENALGO_API_KEY}}", "{{OPENALGO_HOST}}"]
ARGS_API_KEY_INDEX = 0
ARGS_HOST_INDEX = 1

# What render_agent_json() writes when OPENALGO_API_KEY is unset. Never a key
# worth rebinding to.
PLACEHOLDER_API_KEY = "REPLACE_ME"


def _get_logger():
    """Import openalgo's own centralized logger lazily.

    This module is loaded via ``importlib.util.spec_from_file_location`` (see
    the module docstring), so a plain ``from utils.logging import get_logger``
    can fail with ``ModuleNotFoundError`` depending on how the server was
    started. Insert the openalgo root explicitly first, exactly as
    ``mcp/custom_tools.py`` does.

    Note the handler openalgo configures is a bare ``logging.StreamHandler()``,
    i.e. **stderr** -- which is what makes logging from here safe at all under
    the stdio transport, where stdout carries the JSON-RPC stream.
    """
    openalgo_root = str(Path(__file__).resolve().parent.parent)
    if openalgo_root not in sys.path:
        sys.path.insert(0, openalgo_root)
    from utils.logging import get_logger

    return get_logger("openalgo_mcp_key_reload")


logger = _get_logger()


def agent_json_path() -> Path:
    """Locate the MCP client config this server's argv was rendered from.

    ``OPENALGO_MCP_AGENT_JSON`` names the file outright (useful for a test or
    for a client whose config lives somewhere else); otherwise it is
    ``$VIBE_TRADING_HOME/agent.json``, falling back to ``~/.vibe-trading`` --
    the same resolution ``scripts/setup_vibe.py::vibe_home()`` uses to write it.
    """
    override = os.getenv("OPENALGO_MCP_AGENT_JSON", "").strip()
    if override:
        return Path(override).expanduser()
    home = os.getenv("VIBE_TRADING_HOME", "").strip()
    base = Path(home).expanduser() if home else Path.home() / ".vibe-trading"
    return base / "agent.json"


def read_bound_credentials(path: Path) -> tuple[str, str]:
    """Return the ``(api_key, host)`` pair ``path`` would spawn this server with.

    Raises ``ValueError`` on any shape this server cannot bind to -- a missing
    ``openalgo`` entry, too few ``args``, a blank key, the ``REPLACE_ME``
    placeholder. The caller turns that into a loud error and keeps the client it
    already has; it must never be downgraded to "assume the old key is fine".
    """
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object, got {type(payload).__name__}")

    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: 'mcpServers' is missing or not an object")

    entry = servers.get(SERVER_KEY)
    if not isinstance(entry, dict):
        raise ValueError(f"{path}: no '{SERVER_KEY}' server entry")

    args = entry.get("args")
    if not isinstance(args, list) or len(args) <= ARGS_HOST_INDEX:
        raise ValueError(
            f"{path}: '{SERVER_KEY}.args' must be a list of at least "
            f"{ARGS_HOST_INDEX + 1} entries (api_key, host)"
        )

    api_key = args[ARGS_API_KEY_INDEX]
    host = args[ARGS_HOST_INDEX]
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError(f"{path}: '{SERVER_KEY}.args[{ARGS_API_KEY_INDEX}]' is not a non-empty string")
    if not isinstance(host, str) or not host.strip():
        raise ValueError(f"{path}: '{SERVER_KEY}.args[{ARGS_HOST_INDEX}]' is not a non-empty string")

    api_key = api_key.strip()
    if api_key == PLACEHOLDER_API_KEY:
        raise ValueError(
            f"{path}: '{SERVER_KEY}' carries the {PLACEHOLDER_API_KEY} placeholder — "
            "OPENALGO_API_KEY was unset when it was rendered"
        )
    return api_key, host.strip()


def mask(api_key: str) -> str:
    """Loggable fingerprint of a key — enough to tell two keys apart, no more."""
    if len(api_key) <= 4:
        return "*" * len(api_key)
    return f"{'*' * (len(api_key) - 4)}{api_key[-4:]}"


class KeyWatcher:
    """Poll one ``agent.json`` and rebind ``mcpserver``'s client when it rotates.

    ``mcpserver`` is the ``mcp/mcpserver.py`` module object. Only three things
    are used from it -- ``api_key``/``host`` (what is bound now), ``api`` (the
    OpenAlgo SDK constructor it already imported), and ``init_for_http`` (the
    one function in that module that owns the ``global api_key, host, client``
    swap). Nothing here hand-rolls a second swap.
    """

    def __init__(
        self,
        mcpserver: Any,
        *,
        path: Path | str | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self.mcpserver = mcpserver
        self.path = Path(path) if path is not None else agent_json_path()
        self.poll_seconds = float(poll_seconds)
        self._seen_mtime: float | None = None
        self._warned_missing = False

    def _current_mtime(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except FileNotFoundError:
            return None

    def maybe_reload(self) -> bool:
        """Rebind if the config changed on disk and now names a different key.

        Returns ``True`` when the client was rebound, ``False`` when there was
        nothing to do. Propagates whatever a malformed config or a failed client
        construction raises -- ``run_forever`` is what turns that into a logged
        error, so a direct caller (a test, a future ``trade doctor`` probe) still
        sees the real exception.
        """
        mtime = self._current_mtime()
        if mtime is None:
            # No config to watch. Legitimate: the server can be spawned from a
            # client whose own config is elsewhere. Say so once, not every poll.
            if not self._warned_missing:
                logger.warning(
                    "OpenAlgo MCP key watcher: %s does not exist — a rotated "
                    "OPENALGO_API_KEY will not be picked up until it does",
                    self.path,
                )
                self._warned_missing = True
            self._seen_mtime = None
            return False

        self._warned_missing = False
        if mtime == self._seen_mtime:
            return False

        # Record the new mtime *before* parsing: a config we cannot use should
        # be reported once per rewrite, not once per poll forever.
        self._seen_mtime = mtime

        new_key, new_host = read_bound_credentials(self.path)
        if new_key == self.mcpserver.api_key and new_host == self.mcpserver.host:
            return False

        old_key = self.mcpserver.api_key
        # Build first, swap after. If the constructor raises, this propagates
        # with the old client still bound — never a keyless server.
        new_client = self.mcpserver.api(api_key=new_key, host=new_host)
        self.mcpserver.init_for_http(new_key, new_host, client_value=new_client)
        logger.info(
            "OpenAlgo MCP: rebound SDK client from %s to %s (host %s) after %s changed",
            mask(old_key or ""),
            mask(new_key),
            new_host,
            self.path,
        )
        return True

    def run_forever(self, stop: threading.Event | None = None) -> None:
        """Poll until ``stop`` is set. Never raises; logs loudly instead."""
        stop = stop if stop is not None else threading.Event()
        while True:
            try:
                self.maybe_reload()
            except Exception:
                # Loud, with a traceback, on stderr. The old client stays bound;
                # what must not happen is this failing quietly and the server
                # going on serving a key the operator believes it has rotated.
                logger.error(
                    "OpenAlgo MCP key watcher: failed to apply the rotated key from %s — "
                    "the previous key stays bound; fix the file or restart this server",
                    self.path,
                    exc_info=True,
                )
            if stop.wait(self.poll_seconds):
                return


def start_key_watcher(
    mcpserver: Any,
    *,
    path: Path | str | None = None,
    poll_seconds: float | None = None,
) -> threading.Thread | None:
    """Start the watcher on a daemon thread. Returns it, or ``None`` if disabled.

    Called once from ``mcp/mcpserver.py``'s stdio entry point. The HTTP/SSE boot
    path must not call this: ``blueprints/mcp_http.py`` already rotates the
    client through ``init_for_http()`` on its own.
    """
    if poll_seconds is None:
        raw = os.getenv(POLL_ENV_VAR, "").strip()
        poll_seconds = float(raw) if raw else DEFAULT_POLL_SECONDS
    if poll_seconds <= 0:
        logger.info(
            "OpenAlgo MCP key watcher disabled (%s=%s) — a rotated key needs a server restart",
            POLL_ENV_VAR,
            poll_seconds,
        )
        return None

    watcher = KeyWatcher(mcpserver, path=path, poll_seconds=poll_seconds)
    thread = threading.Thread(
        target=watcher.run_forever,
        name="openalgo-mcp-key-watcher",
        daemon=True,
    )
    thread.start()
    logger.info(
        "OpenAlgo MCP key watcher started on %s (every %.1fs)", watcher.path, poll_seconds
    )
    return thread
