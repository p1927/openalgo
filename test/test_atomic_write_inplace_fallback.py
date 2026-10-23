"""utils/atomic_write: atomic by default, in-place when a rename cannot work (Docker bind mount)."""

import errno
import os

import pytest

from utils import atomic_write


def test_plain_write_is_atomic_and_leaves_no_temp(tmp_path):
    target = tmp_path / "s.json"
    target.write_text("old")
    os.chmod(target, 0o640)
    atomic_write.atomic_write_text(target, "new")
    assert target.read_text() == "new"
    assert (target.stat().st_mode & 0o777) == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


def test_bind_mounted_target_is_rewritten_in_place_same_inode(tmp_path, monkeypatch):
    target = tmp_path / "s.json"
    target.write_text("old-and-longer")
    inode = target.stat().st_ino
    monkeypatch.setattr(atomic_write, "_is_bind_mounted", lambda _p: True)
    monkeypatch.setattr(os, "replace", lambda *_a: pytest.fail("rename attempted on a bind mount"))
    atomic_write.atomic_write_text(target, "new")
    assert target.read_text() == "new"
    assert target.stat().st_ino == inode


@pytest.mark.parametrize("err", [errno.EXDEV, errno.EBUSY, errno.EACCES, errno.EPERM])
def test_rename_refused_falls_back_to_in_place(tmp_path, monkeypatch, err):
    target = tmp_path / "s.json"
    target.write_text("old")

    def refuse(*_a):
        raise OSError(err, "refused")

    monkeypatch.setattr(os, "replace", refuse)
    atomic_write.atomic_write_text(target, "new")
    assert target.read_text() == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]  # temp cleaned up


def test_unrelated_rename_error_still_raises_and_keeps_old_file(tmp_path, monkeypatch):
    target = tmp_path / "s.json"
    target.write_text("old")

    def boom(*_a):
        raise OSError(errno.ENOSPC, "full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write.atomic_write_text(target, "new")
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


def test_unwritable_directory_for_temp_falls_back_in_place(tmp_path, monkeypatch):
    target = tmp_path / "s.json"
    target.write_text("old")
    real_open = os.open

    def deny_tmp(p, *a, **k):
        if str(p).endswith(".tmp"):
            raise PermissionError(errno.EACCES, "denied")
        return real_open(p, *a, **k)

    monkeypatch.setattr(os, "open", deny_tmp)
    atomic_write.atomic_write_text(target, "new")
    assert target.read_text() == "new"
