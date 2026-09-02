"""PID-lock guard against a duplicate ``python app.py`` dev-server instance.

Multiple concurrent sessions (local + cloud) working against the same
checkout have repeatedly started a second copy of the dev server on top of
an already-running one. The two then fight over the WebSocket proxy port
(8765) and each other's cleanup/restart logic, taking turns killing each
other's active connections. Call :func:`acquire_service_lock` once, early,
from the ``if __name__ == "__main__":`` dev-server entrypoint (before
touching the network) so a second instance refuses to start instead of
racing the first.

Gunicorn/eventlet production deployments never execute this module — gunicorn
imports ``app`` as a WSGI callable and never runs ``__main__`` — so this only
guards the single-process dev server, which is the path that actually gets
started by hand.
"""

from __future__ import annotations

import atexit
import os
import sys
import time
from pathlib import Path


def _lock_dir() -> Path:
    d = Path.home() / ".vibe-trading" / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check. A dead PID means the lock is stale."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, just owned by someone else — treat as alive rather than guess.
        return True
    except OSError:
        return False
    return True


def acquire_service_lock(name: str, *, port: int | None = None) -> None:
    """Exit the process if another live instance of ``name`` already holds
    this lock; otherwise claim it for this process.

    Writes ``~/.vibe-trading/locks/<name>-<port>.lock`` (pid / start time /
    port) — keyed by port, not just name, so a `trade release` instance of
    this app (a different port, by design) never collides with a `trade
    dev`/`trade up` instance; only two processes genuinely trying to bind the
    *same* port (the real incident this guards against) block each other.
    A stale lock (owning PID no longer alive) is reclaimed silently. The
    lock is released via ``atexit`` on normal interpreter shutdown, which
    also covers this app's own graceful SIGTERM handler
    (``run_websocket_server``'s cleanup path ends in a normal exit). A hard
    SIGKILL or crash leaves a stale lock that the next startup's liveness
    check reclaims automatically, so this never permanently wedges.

    Args:
        name: Stable service identifier, e.g. ``"openalgo"``.
        port: Listen port, included in the block message for operators.
    """
    lock_key = f"{name}-{port}" if port else name
    lock_path = _lock_dir() / f"{lock_key}.lock"
    if lock_path.exists():
        existing_pid = -1
        existing_started = "unknown"
        try:
            lines = lock_path.read_text().splitlines()
            existing_pid = int(lines[0])
            existing_started = lines[1] if len(lines) > 1 else "unknown"
        except (ValueError, IndexError, OSError):
            pass
        if _pid_alive(existing_pid):
            port_txt = f" on port {port}" if port else ""
            sys.stderr.write(
                f"\n\033[91m\033[1mREFUSING TO START: '{name}'{port_txt} is "
                f"already running\033[0m\n"
                f"\033[91mPID {existing_pid}, started {existing_started}. "
                f"Another session (local or cloud) already has the dev "
                f"server up — use it instead of starting a second one.\n"
                f"If you're sure it crashed without cleaning up: "
                f"kill {existing_pid} (or rm {lock_path} once you've "
                f"confirmed PID {existing_pid} is really gone).\033[0m\n\n"
            )
            sys.exit(1)
        # Stale lock — owning process is gone. Fall through and reclaim it.

    lock_path.write_text(f"{os.getpid()}\n{time.strftime('%Y-%m-%dT%H:%M:%S')}\n{port or ''}\n")

    def _release() -> None:
        try:
            if lock_path.read_text().splitlines()[0] == str(os.getpid()):
                lock_path.unlink()
        except (OSError, IndexError):
            pass

    atexit.register(_release)
