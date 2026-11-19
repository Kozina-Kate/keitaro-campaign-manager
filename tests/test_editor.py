import copy
import json

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def remote_offer(offer_id, share):
    return {"offer_id": offer_id, "share": share, "state": "active"}


class EditorTracker:
    def __init__(self):
        self.name = "test_kozina_campaign1"
        self.puts = []
        self.timeout = None
        self.streams = [
            {
                "id": 101,
                "campaign_id": 42,
                "name": "Google",
                "schema": "redirect",
                "position": 1,
                "filters": [{"name": "country", "payload": ["AU"]}],
                "offers": [],
            },
            {
                "id": 102,
                "campaign_id": 42,
                "name": "Offers",
                "schema": "landings",
                "position": 2,
                "filters": [],
                "landings": [],
                "offers": [
                    remote_offer(1, 34),
                    remote_offer(2, 33),
                    remote_offer(3, 33),
                ],
            },
        ]

    def __call__(self, request):
        path = request.url.path.removeprefix("/admin_api/v1/")
        if request.method == "GET":
            if path == "campaigns/42":
                return httpx.Response(200, json={"id": 42, "name": self.name})
            if path == "campaigns/42/streams":
                return httpx.Response(200, json=self.streams)
            if path == "offers":
                return httpx.Response(
                    200,
                    json=[
                        {"id": i, "name": f"Offer {i}", "state": "active"}
                        for i in range(1, 5)
                    ],
                )
            if path.startswith("offers/"):
                offer_id = int(path.split("/")[-1])
                if offer_id not in range(1, 5):
                    return httpx.Response(404)
                return httpx.Response(
                    200,
                    json={
                        "id": offer_id,
                        "name": f"Offer {offer_id}",
                        "state": "active",
                    },
                )
            if path.startswith("streams/"):
                stream = next(
                    row for row in self.streams if row["id"] == int(path.split("/")[-1])
                )
                return httpx.Response(200, json=stream)
            raise AssertionError(path)
        assert request.method == "PUT" and path == "streams/102"
        payload = json.loads(request.content)
        assert set(payload) == {"offers"}
        self.puts.append(payload)
        if self.timeout == "before":
            raise httpx.ReadTimeout("private timeout", request=request)
        self.streams[1]["offers"] = payload["offers"]
        if self.timeout == "after":
            raise httpx.ReadTimeout("private timeout", request=request)
        return httpx.Response(200, json=self.streams[1])


@pytest.fixture
def editor(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'editor.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    settings = Settings(_env_file=None, database_url=url, keitaro_api_key="test-secret")
    tracker = EditorTracker()
    with TestClient(create_app(settings, httpx.MockTransport(tracker))) as client:
        response = client.post("/api/editor/campaigns/42/fetch")
        assert response.status_code == 200, response.text
        yield client, tracker, settings


def stream(client):
    return next(
        s
        for s in client.get("/api/editor/campaigns/42").json()["streams"]
        if s["id"] == 102
    )


def change(client, action, expected=200, **values):
    revision = stream(client)["revision"]
    response = client.post(
        f"/api/editor/streams/102/{action}", json={"revision": revision, **values}
    )
    assert response.status_code == expected, response.text
    return response.json()


def shares(state):
    return {row["offer_id"]: row["share"] for row in state["offers"]}


def test_draft_cancel_publish_archive_and_restore(editor):
    client, tracker, _ = editor
    original = copy.deepcopy(tracker.streams)
    state = change(client, "add", offer_id=4)
    assert state["dirty"] and shares(state) == {1: 25, 2: 25, 3: 25, 4: 25}
    assert tracker.streams == original and not tracker.puts
    assert client.post("/api/editor/campaigns/42/fetch").status_code == 409
    state = change(client, "cancel")
    assert not state["dirty"] and 4 not in shares(state)
    assert not tracker.puts
    change(client, "add", offer_id=4)
    state = change(client, "publish")
    assert not state["dirty"] and len(tracker.streams[1]["offers"]) == 4
    state = change(client, "remove", offer_id=1)
    assert shares(state) == {1: 0, 2: 34, 3: 33, 4: 33}
    assert state["offers"][0]["removed"]
    change(client, "publish")
    assert len(tracker.streams[1]["offers"]) == 3
    assert stream(client)["offers"][0]["removed"]
    state = change(client, "restore", offer_id=1)
    assert shares(state) == {1: 25, 2: 25, 3: 25, 4: 25}
    change(client, "publish")
    assert len(tracker.streams[1]["offers"]) == 4
    assert tracker.streams[0] == original[0]


def test_pins_and_integer_remainder(editor):
    client, _, _ = editor
    state = change(client, "pin", offer_id=1, share=25)
    assert shares(state) == {1: 25, 2: 38, 3: 37}
    state = change(client, "add", offer_id=4)
    assert shares(state) == {1: 25, 2: 25, 3: 25, 4: 25}
    state = change(client, "remove", offer_id=2)
    assert shares(state) == {1: 25, 2: 0, 3: 38, 4: 37}
    previous = stream(client)
    change(client, "pin", offer_id=3, share=90, expected=422)
    assert stream(client) == previous
    state = change(client, "publish")
    assert state["offers"][0]["pinned"]
    change(client, "unpin", offer_id=1)
    state = change(client, "cancel")
    assert state["offers"][0]["pinned"] and shares(state)[1] == 25


def test_external_removal_moves_offer_to_archive(editor):
    client, tracker, _ = editor
    tracker.streams[1]["offers"] = [remote_offer(2, 50), remote_offer(3, 50)]
    response = client.post("/api/editor/campaigns/42/fetch")
    assert response.status_code == 200
    state = stream(client)
    assert not state["dirty"] and state["offers"][0]["removed"]
    assert shares(state) == {1: 0, 2: 50, 3: 50}
    change(client, "restore", offer_id=1)
    change(client, "publish")
    assert len(tracker.streams[1]["offers"]) == 3


def test_external_edit_is_not_overwritten(editor):
    client, tracker, _ = editor
    change(client, "add", offer_id=4)
    tracker.streams[1]["offers"] = [remote_offer(1, 100)]
    change(client, "publish", expected=409)
    assert not tracker.puts
    assert stream(client)["status"] == "conflict"
    assert stream(client)["dirty"]
    change(client, "cancel")
    assert client.post("/api/editor/campaigns/42/fetch").status_code == 200
    assert shares(stream(client)) == {1: 100, 2: 0, 3: 0}


def test_last_offer_can_be_removed_in_draft_but_not_published(editor):
    client, tracker, _ = editor
    for offer_id in (1, 2, 3):
        state = change(client, "remove", offer_id=offer_id)
    assert state["total_share"] == 0 and not state["can_publish"]
    change(client, "publish", expected=422)
    assert not tracker.puts
    state = change(client, "cancel")
    assert state["total_share"] == 100 and not state["dirty"]


@pytest.mark.parametrize("timing", ["before", "after"])
def test_uncertain_publish_is_reconciled_without_repeated_put(editor, timing):
    client, tracker, _ = editor
    change(client, "add", offer_id=4)
    tracker.timeout = timing
    change(client, "publish", expected=504)
    assert stream(client)["status"] == "uncertain"
    change(client, "cancel", expected=409)
    change(client, "publish", expected=409)
    state = change(client, "reconcile")
    assert len(tracker.puts) == 1
    assert state["status"] == "ready"
    assert state["dirty"] == (timing == "before")


def test_stale_revision_duplicate_and_unknown_offer(editor):
    client, tracker, _ = editor
    old = stream(client)["revision"]
    change(client, "add", offer_id=4)
    response = client.post(
        "/api/editor/streams/102/remove", json={"revision": old, "offer_id": 1}
    )
    assert response.status_code == 409
    change(client, "add", offer_id=4, expected=409)
    change(client, "add", offer_id=999, expected=422)
    assert not tracker.puts


def test_redirect_flow_not_edited_and_prefix_is_enforced(editor):
    client, tracker, _ = editor
    redirect = client.get("/api/editor/campaigns/42").json()["streams"][0]
    assert not redirect["editable"] and not redirect["offers"]
    assert (
        client.post(
            "/api/editor/streams/101/add",
            json={"revision": redirect["revision"], "offer_id": 4},
        ).status_code
        == 422
    )
    tracker.name = "someone_else_campaign"
    assert client.post("/api/editor/campaigns/42/fetch").status_code == 403
    assert not tracker.puts


def test_draft_and_pins_survive_restart(editor):
    client, tracker, settings = editor
    change(client, "pin", offer_id=1, share=25)
    change(client, "add", offer_id=4)
    before = stream(client)
    with TestClient(create_app(settings, httpx.MockTransport(tracker))) as restarted:
        assert stream(restarted) == before
        assert restarted.get("/editor").status_code == 200
        assert restarted.get("/api/editor/offers?q=Offer%204").json() == [
            {"id": 4, "name": "Offer 4"}
        ]


def test_all_pins_must_sum_to_100_and_zero_weight_is_valid(editor):
    client, _, _ = editor
    change(client, "pin", offer_id=1, share=25)
    change(client, "pin", offer_id=2, share=25)
    previous = stream(client)
    change(client, "pin", offer_id=3, share=25, expected=422)
    assert stream(client) == previous
    state = change(client, "pin", offer_id=3, share=50)
    assert state["total_share"] == 100
    change(client, "unpin", offer_id=3)
    change(client, "unpin", offer_id=2)
    state = change(client, "pin", offer_id=1, share=100)
    assert shares(state) == {1: 100, 2: 0, 3: 0}


def test_publication_stops_if_campaign_was_renamed(editor):
    client, tracker, _ = editor
    change(client, "add", offer_id=4)
    tracker.name = "another_campaign"
    change(client, "publish", expected=403)
    assert not tracker.puts
    assert stream(client)["status"] == "ready"
    assert stream(client)["dirty"]


def test_deleted_stream_stays_in_local_history(editor):
    client, tracker, _ = editor
    tracker.streams.pop()
    assert client.post("/api/editor/campaigns/42/fetch").status_code == 200
    state = stream(client)
    assert not state["remote_exists"] and not state["editable"]
    change(client, "publish", expected=409)
    assert not tracker.puts
