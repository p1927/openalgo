import sys
from types import SimpleNamespace

import pytest

from broker.stock_simulator.api import _mc_rebuild as rebuild


@pytest.mark.parametrize("status", ["success", "error", "downloading", "unknown", None])
def test_finished_thread_requires_confirmed_owned_success(monkeypatch,status):
    monkeypatch.setitem(sys.modules,"utils.auth_utils",SimpleNamespace(async_master_contract_download=lambda _:None))
    monkeypatch.setitem(sys.modules,"database.master_contract_status_db",SimpleNamespace(get_status=lambda _:{"status":status}))
    assert rebuild.trigger_mc_rebuild_if_date_changed("2026-10-19","2026-10-20") == ("completed" if status=="success" else "failed")

def test_thread_exception_cannot_report_completion(monkeypatch):
    def failed(_): raise OSError("download unavailable")
    monkeypatch.setitem(sys.modules,"utils.auth_utils",SimpleNamespace(async_master_contract_download=failed))
    monkeypatch.setitem(sys.modules,"database.master_contract_status_db",SimpleNamespace(get_status=lambda _:{"status":"success"}))
    assert rebuild.trigger_mc_rebuild_if_date_changed("2026-10-19","2026-10-20") == "failed"
