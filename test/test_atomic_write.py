"""utils.atomic_write (Trade fork sidecar): a reader sees the old file or the new one; mode kept; no stray temp."""

import os
import stat

import pytest

from utils.atomic_write import atomic_write_text


def test_content_mode_and_no_temp(tmp_path):
    target = tmp_path / "store" / "w.json"  # parent created
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text() == '{"a": 1}'
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o644
    target.chmod(0o600)
    atomic_write_text(target, "x")
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600  # a replace must not widen a private file
    assert not [p for p in target.parent.iterdir() if p.name.endswith(".tmp")]


def test_failed_replace_keeps_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "w.json"
    target.write_text("old")
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        atomic_write_text(target, "new")
    assert target.read_text() == "old"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_custom_tools_widget_store_uses_it():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "mcp" / "custom_tools.py").read_text()
    assert "store.write_text(" not in src and src.count("atomic_write_text(store,") == 3
