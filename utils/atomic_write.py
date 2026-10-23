"""Atomic file write for fork-owned stores (Trade fork sidecar).

OpenAlgo runs in its own venv and cannot import ``trade_integrations``, so this is the fork's one vendored copy
of ``trade_integrations.context.hub.atomic_write_text`` (same behaviour): unique temp file in the destination
directory, every byte written, fsync, ``os.replace`` and a directory fsync, keeping the destination's permission
bits. A concurrent reader sees the old file or the new one, never a torn half.

A Docker single-file bind mount (or a root-owned parent directory) cannot take a rename: it fails with
EXDEV/EBUSY/EACCES/EPERM, or on Docker Desktop silently shadows the mount. There the write goes in place
(open, truncate, write, fsync) through the real inode, the same fallback upstream ``utils.env_check`` uses for ``.env``
(its device check is reused, not copied, so there is one definition of "bind-mounted").
"""

from __future__ import annotations

import errno
import os
import uuid
from pathlib import Path


# The rename pattern cannot work here; fall back to an in-place rewrite (same set as utils.env_check).
_INPLACE_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.EXDEV, errno.EBUSY})


def _write_in_place(path: Path, data: bytes, durable: bool) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        pending = memoryview(data)
        while pending:
            pending = pending[os.write(fd, pending) :]
        if durable:
            os.fsync(fd)
    finally:
        os.close(fd)


def _is_bind_mounted(path: Path) -> bool:
    try:
        from utils.env_check import _target_needs_inplace_write
    except Exception:  # env_check is upstream; never let it break a store write
        return False
    return _target_needs_inplace_write(str(path))


def atomic_write_text(path: Path | str, content: str, *, durable: bool = True) -> None:
    path = Path(path)
    data = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if _is_bind_mounted(path):
        _write_in_place(path, data, durable)
        return
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    except OSError as exc:
        if exc.errno not in _INPLACE_ERRNOS:
            raise
        _write_in_place(path, data, durable)
        return
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
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if exc.errno not in _INPLACE_ERRNOS:
            raise
        _write_in_place(path, data, durable)
        return
    if durable:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
