import json
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class Tracker:
    def __init__(self):
        self.writes = []
        self.campaign = None
        self.streams = []
        self.failure = None

    def __call__(self, request):
        path = request.url.path.removeprefix("/admin_api/v1/")
        if request.method == "GET":
            if path in ("domains", "groups", "offers", "traffic_sources"):
                data = [{"id": 1, "name": "example.com"}]
            elif path == "domains/1":
                data = {
                    "id": 1,
                    "name": "example.com",
                    "is_ssl": True,
                    "state": "active",
                }
            elif path == "offers/1":
                data = {"id": 1, "state": "active"}
            elif path == "campaigns/42":
                data = dict(self.campaign)
                if self.failure == "verify":
                    data["type"] = "weight"
            elif path == "campaigns/42/streams":
                data = self.streams
            else:
                raise AssertionError(path)
            return httpx.Response(200, json=data)

        assert request.method == "POST"
        data = json.loads(request.content) if request.content else None
        self.writes.append((path, data))
        if path == "campaigns":
            if self.failure == "reject":
                return httpx.Response(406, text="private error")
            self.campaign = {**data, "id": 42}
            if self.failure == "create_timeout":
                raise httpx.ReadTimeout("private timeout", request=request)
            return httpx.Response(201, json=self.campaign)
        if path == "streams":
            if len(self.streams) == 1 and self.failure == "stream2":
                return httpx.Response(500, text="private error")
            stream = {**data, "id": 101 + len(self.streams)}
            self.streams.append(stream)
            return httpx.Response(200, json=stream)
        if path == "campaigns/42/enable":
            assert len(self.streams) == 2
            self.campaign["state"] = "active"
            if self.failure == "enable_timeout":
                raise httpx.ReadTimeout("private timeout", request=request)
            return httpx.Response(200, json={"success": True})
        raise AssertionError(path)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'campaigns.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    settings = Settings(_env_file=None, database_url=url, keitaro_api_key="test-secret")
    tracker = Tracker()
    return settings, tracker


def payload(**changes):
    return {
        "request_id": str(uuid4()),
        "name": "test_kozina_campaign1",
        "country_code": "au",
        "offer_id": 1,
        "domain_id": 1,
        "group_id": 1,
        "traffic_source_id": 1,
        **changes,
    }


def test_create_routing_and_replay_after_restart(setup):
    settings, tracker = setup
    body = payload()
    with TestClient(create_app(settings, httpx.MockTransport(tracker))) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        response = client.post("/api/campaigns", json=body)
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["status"] == "succeeded"
        assert result["campaign_id"] == 42
        assert result["stream_ids"] == [101, 102]
        assert result["campaign_url"].startswith("https://example.com/tk-")
        assert tracker.writes[0][1]["state"] == "disabled"
        geo, fallback = tracker.streams
        assert geo["filters"] == [
            {"name": "country", "mode": "accept", "payload": ["AU"]}
        ]
        assert geo["position"] < fallback["position"]
        assert fallback["offers"] == [{"offer_id": 1, "share": 100, "state": "active"}]
        assert [path for path, _ in tracker.writes] == [
            "campaigns",
            "streams",
            "streams",
            "campaigns/42/enable",
        ]
        assert client.get(f"/api/operations/{body['request_id']}").json() == result
    # Результат сохраняется в БД и не зависит от процесса FastAPI.
    with TestClient(create_app(settings, httpx.MockTransport(tracker))) as client:
        assert client.post("/api/campaigns", json=body).json() == result
        changed = {**body, "name": "test_kozina_different"}
        assert client.post("/api/campaigns", json=changed).status_code == 409
    assert len(tracker.writes) == 4


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "campaign1"},
        {"country_code": "ZZ"},
        {"offer_id": 0},
        {"domain_id": True},
        {"offer_id": "1"},
        {"extra": "unexpected"},
    ],
)
def test_invalid_input_never_writes(setup, changes):
    settings, tracker = setup
    with TestClient(create_app(settings, httpx.MockTransport(tracker))) as client:
        assert client.post("/api/campaigns", json=payload(**changes)).status_code == 422
    assert tracker.writes == []


def test_unavailable_reference_fails_before_creation(setup):
    settings, tracker = setup
    with TestClient(create_app(settings, httpx.MockTransport(tracker))) as client:
        response = client.post("/api/campaigns", json=payload(offer_id=999))
    assert response.status_code == 422
    assert response.json()["status"] == "failed"
    assert tracker.writes == []


@pytest.mark.parametrize("failure", ["stream2", "verify", "create_timeout", "reject"])
def test_partial_failures_are_saved_and_not_retried(setup, failure):
    settings, tracker = setup
    tracker.failure = failure
    body = payload()
    with TestClient(create_app(settings, httpx.MockTransport(tracker))) as client:
        response = client.post("/api/campaigns", json=body)
        assert response.status_code in (422, 502), response.text
        result = response.json()
        assert result["status"] == ("failed" if failure == "reject" else "partial")
        if failure in ("stream2", "verify"):
            assert result["campaign_id"] == 42
            assert tracker.campaign["state"] == "disabled"
        assert "private" not in response.text
        count = len(tracker.writes)
        assert client.post("/api/campaigns", json=body).status_code == 409
        assert len(tracker.writes) == count
        assert (
            client.get(f"/api/operations/{body['request_id']}").json()["status"]
            == result["status"]
        )
    assert not any(path.endswith("/enable") for path, _ in tracker.writes)


def test_unknown_operation(setup):
    settings, tracker = setup
    with TestClient(create_app(settings, httpx.MockTransport(tracker))) as client:
        assert client.get(f"/api/operations/{uuid4()}").status_code == 404


def test_reconcile_after_enable_timeout_uses_only_reads(setup):
    settings, tracker = setup
    tracker.failure = "enable_timeout"
    body = payload()
    with TestClient(create_app(settings, httpx.MockTransport(tracker))) as client:
        response = client.post("/api/campaigns", json=body)
        assert response.json()["status"] == "partial"
        writes = len(tracker.writes)
        response = client.post(f"/api/operations/{body['request_id']}/verify")
        assert response.json()["status"] == "succeeded"
        assert response.json()["error_code"] is None
        assert len(tracker.writes) == writes
        assert client.post("/api/campaigns", json=body).json()["status"] == "succeeded"
        assert len(tracker.writes) == writes
