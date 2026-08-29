"""Route-level tests for restx_api/scheduler_registry.py — the apikey-authed
endpoints Trade's unified scheduler registry (vibetrading-agent's
OpenAlgoClient) calls to list/pause/resume jobs across openalgo's five
scheduler instances. Service-logic edge cases are covered by
test_scheduler_registry_service.py; this file is request/response wiring
only.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask_restx import Api

import restx_api.scheduler_registry as scheduler_registry_api
from limiter import limiter


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    monkeypatch.setattr(limiter, "enabled", False)
    rest_api = Api(app)
    rest_api.add_namespace(scheduler_registry_api.api, path="/scheduler/registry")
    return app.test_client()


def test_list_rejects_an_invalid_api_key(client, monkeypatch):
    monkeypatch.setattr(
        scheduler_registry_api,
        "list_scheduler_registry",
        lambda api_key: (False, {"status": "error", "message": "Invalid openalgo apikey"}, 403),
    )

    response = client.post("/scheduler/registry/", json={"apikey": "bad-key"})

    assert response.status_code == 403
    assert response.get_json()["message"] == "Invalid openalgo apikey"


def test_list_returns_entries_on_success(client, monkeypatch):
    monkeypatch.setattr(
        scheduler_registry_api,
        "list_scheduler_registry",
        lambda api_key: (True, {"status": "success", "data": {"entries": [{"id": "C:flow:wf_1"}]}}, 200),
    )

    response = client.post("/scheduler/registry/", json={"apikey": "key"})

    assert response.status_code == 200
    assert response.get_json()["data"]["entries"] == [{"id": "C:flow:wf_1"}]


def test_list_handles_missing_json_body(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        scheduler_registry_api,
        "list_scheduler_registry",
        lambda api_key: captured.setdefault("api_key", api_key) or (True, {"status": "success"}, 200),
    )

    response = client.post("/scheduler/registry/", json={})

    assert response.status_code == 200
    assert captured["api_key"] is None


def test_pause_forwards_source_and_job_id(client, monkeypatch):
    captured = {}

    def fake_pause(api_key, source, job_id):
        captured.update(api_key=api_key, source=source, job_id=job_id)
        return True, {"status": "success", "message": "Job paused"}, 200

    monkeypatch.setattr(scheduler_registry_api, "pause_scheduler_job", fake_pause)

    response = client.post(
        "/scheduler/registry/pause", json={"apikey": "key", "source": "flow", "job_id": "wf_1"}
    )

    assert response.status_code == 200
    assert captured == {"api_key": "key", "source": "flow", "job_id": "wf_1"}


def test_resume_forwards_source_and_job_id(client, monkeypatch):
    captured = {}

    def fake_resume(api_key, source, job_id):
        captured.update(api_key=api_key, source=source, job_id=job_id)
        return True, {"status": "success", "message": "Job resumed"}, 200

    monkeypatch.setattr(scheduler_registry_api, "resume_scheduler_job", fake_resume)

    response = client.post(
        "/scheduler/registry/resume",
        json={"apikey": "key", "source": "historify", "job_id": "sched_2"},
    )

    assert response.status_code == 200
    assert captured == {"api_key": "key", "source": "historify", "job_id": "sched_2"}


def test_pause_propagates_failure_status_code(client, monkeypatch):
    monkeypatch.setattr(
        scheduler_registry_api,
        "pause_scheduler_job",
        lambda api_key, source, job_id: (False, {"status": "error", "message": "boom"}, 400),
    )

    response = client.post(
        "/scheduler/registry/pause", json={"apikey": "key", "source": "flow", "job_id": "wf_1"}
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "boom"


def test_stream_rejects_when_validate_stream_access_fails(client, monkeypatch):
    monkeypatch.setattr(
        scheduler_registry_api,
        "validate_stream_access",
        lambda api_key, source: (False, "Invalid openalgo apikey"),
    )

    response = client.get("/scheduler/registry/flow/wf_1/stream?apikey=bad")

    assert response.status_code == 403
    assert response.get_json()["message"] == "Invalid openalgo apikey"


def test_stream_rejects_a_source_without_live_log_support(client, monkeypatch):
    monkeypatch.setattr(
        scheduler_registry_api,
        "validate_stream_access",
        lambda api_key, source: (False, f"Live-log-tail not available for source: {source!r}"),
    )

    response = client.get("/scheduler/registry/strategy/job_1/stream?apikey=key")

    assert response.status_code == 403
    assert "strategy" in response.get_json()["message"]


def test_stream_returns_event_stream_response_on_success(client, monkeypatch):
    monkeypatch.setattr(
        scheduler_registry_api, "validate_stream_access", lambda api_key, source: (True, "")
    )
    monkeypatch.setattr(
        scheduler_registry_api,
        "stream_scheduler_run_log",
        lambda job_id: iter(['event: log\ndata: {"seq": 1}\n\n']),
    )

    # Note: does not read the response body — `stream_scheduler_run_log` is a
    # real infinite generator in production, and consuming it here (even the
    # fake finite one above) isn't the point of this test; the route wiring
    # (auth gate passed, right mimetype/headers) is.
    response = client.get("/scheduler/registry/flow/wf_1/stream?apikey=key")

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert response.headers["Cache-Control"] == "no-cache"
