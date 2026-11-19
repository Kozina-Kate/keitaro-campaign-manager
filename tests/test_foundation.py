import asyncio

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.keitaro import KeitaroClient, KeitaroError
from app.main import create_app
from app.schemas import CampaignName


def settings(**kwargs):
    return Settings(_env_file=None, keitaro_api_key="test-secret", **kwargs)


@pytest.mark.parametrize("name", ["campaign1", "test_ivanov_campaign1", "test_kozina_"])
def test_rejects_wrong_campaign_prefix(name):
    with pytest.raises(ValidationError):
        CampaignName(name=name)


def test_accepts_campaign_prefix():
    assert CampaignName(name=" test_kozina_campaign1 ").name == "test_kozina_campaign1"


@pytest.mark.parametrize("url", ["http://example.com", "https://example.com/admin"])
def test_rejects_incorrect_tracker_url(url):
    with pytest.raises(ValidationError):
        settings(keitaro_base_url=url)


def test_references_use_correct_paths_and_strip_private_fields():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Api-Key"] == "test-secret"
        assert request.method == "GET"
        return httpx.Response(
            200, json=[{"id": 1, "name": "Example", "secret": "hidden"}]
        )

    with TestClient(create_app(settings(), httpx.MockTransport(handler))) as client:
        response = client.get("/api/reference-data")
    assert response.status_code == 200
    assert response.json()["offers"] == [{"id": 1, "name": "Example"}]
    assert {r.url.path for r in calls} == {
        "/admin_api/v1/domains",
        "/admin_api/v1/groups",
        "/admin_api/v1/traffic_sources",
        "/admin_api/v1/offers",
    }
    groups_request = next(r for r in calls if r.url.path.endswith("/groups"))
    assert groups_request.url.params["type"] == "campaigns"


@pytest.mark.parametrize("status", [302, 401, 402, 403, 500])
def test_api_errors_do_not_expose_response_or_key(status):
    def handler(request):
        return httpx.Response(status, text="test-secret: internal server details")

    with TestClient(create_app(settings(), httpx.MockTransport(handler))) as client:
        response = client.get("/api/reference-data")
    assert response.status_code == 502
    assert "test-secret" not in response.text
    assert "internal server details" not in response.text


def test_timeout_is_reported():
    def handler(request):
        raise httpx.ReadTimeout("private details", request=request)

    with TestClient(create_app(settings(), httpx.MockTransport(handler))) as client:
        response = client.get("/api/reference-data")
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "timeout"


@pytest.mark.parametrize("payload", [{"data": []}, ["invalid"], [{"id": 1}]])
def test_malformed_references_are_reported(payload):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    with TestClient(create_app(settings(), transport)) as client:
        response = client.get("/api/reference-data")
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "invalid_response"


def test_missing_key_does_not_make_network_request():
    def handler(request):
        pytest.fail("Запрос без API-ключа не должен уходить в сеть")

    async def run():
        client = KeitaroClient(
            Settings(_env_file=None, keitaro_api_key=""), httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(KeitaroError, match="Не задан"):
                await client.get_list("domains")
        finally:
            await client.close()

    asyncio.run(run())


def test_migrations_and_database_readiness(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    app = create_app(settings(database_url=url))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 503
        command.upgrade(config, "head")
        assert client.get("/health").json() == {"status": "ok"}
        command.check(config)
        command.downgrade(config, "base")
        assert client.get("/health").status_code == 503
        command.upgrade(config, "head")
        assert client.get("/health").status_code == 200
