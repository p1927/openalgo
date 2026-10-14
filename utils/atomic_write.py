"""Atomic file write for fork-owned stores (Trade fork sidecar).

OpenAlgo runs in its own venv and cannot import ``trade_integrations``, so this is the fork's one vendored copy
of ``trade_integrations.context.hub.atomic_write_text`` (same behaviour): unique temp file in the destination
directory, every byte written, fsync, ``os.replace`` and a directory fsync, keeping the destination's permission
bits. A concurrent reader sees the old file or the new one, never a torn half.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def atomic_write_text(path: Path | str, content: str, *, durable: bool = True) -> None:
    path = Path(path)
    data = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        try:
            os.fchmod(fd, os.stat(path).st_mode & 0o7777)
        except FileNotFoundError:
            pass
        pending = memoryview(data)
        while pending:
            pending = pending[os.write(fd, pending) :]
        if durable:
            os.fsync(fd)
    except BaseException:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    os.close(fd)
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    if durable:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
