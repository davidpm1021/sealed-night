from pathlib import Path
from fastapi.testclient import TestClient

from app.main import create_app
from app.providers.demo import DemoProvider
from app.store import EventStore


def test_prerelease_flow(tmp_path: Path):
    app = create_app(DemoProvider(), EventStore(tmp_path / "events"))
    client = TestClient(app)

    created = client.post("/api/events", json={"host_name": "Dave", "set_code": "DEMO"})
    assert created.status_code == 200, created.text
    host = created.json()
    code = host["event"]["code"]

    joined = client.post(f"/api/events/{code}/join", json={"player_name": "Friend"})
    assert joined.status_code == 200
    friend = joined.json()

    started = client.post(
        f"/api/events/{code}/start",
        json={"player_id": host["player_id"], "token": host["token"]},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "deckbuilding"

    kit_resp = client.get(
        f"/api/events/{code}/players/{host['player_id']}/kit",
        params={"token": host["token"]},
    )
    assert kit_resp.status_code == 200
    kit = kit_resp.json()["kit"]
    assert len(kit["packs"]) == 6
    assert kit["promo"]["foil"] is True
    assert all(len(pack["cards"]) == 14 for pack in kit["packs"])

    # A friend's session cannot fetch Dave's private sealed pool.
    private = client.get(
        f"/api/events/{code}/players/{host['player_id']}/kit",
        params={"token": friend["token"]},
    )
    assert private.status_code == 401

    # Start is idempotent and does not reroll kits.
    before = [c["copy_id"] for p in kit["packs"] for c in p["cards"]]
    client.post(
        f"/api/events/{code}/start",
        json={"player_id": host["player_id"], "token": host["token"]},
    )
    again = client.get(
        f"/api/events/{code}/players/{host['player_id']}/kit",
        params={"token": host["token"]},
    ).json()["kit"]
    after = [c["copy_id"] for p in again["packs"] for c in p["cards"]]
    assert before == after

    main_ids = before[:23]
    saved = client.put(
        f"/api/events/{code}/deck",
        json={
            "player_id": host["player_id"],
            "token": host["token"],
            "main_copy_ids": main_ids,
            "basics": {"Plains": 9, "Island": 8},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["card_count"] == 40


def test_non_host_cannot_start(tmp_path: Path):
    app = create_app(DemoProvider(), EventStore(tmp_path / "events"))
    client = TestClient(app)
    host = client.post("/api/events", json={"host_name": "Host", "set_code": "DEMO"}).json()
    code = host["event"]["code"]
    guest = client.post(f"/api/events/{code}/join", json={"player_name": "Guest"}).json()
    resp = client.post(f"/api/events/{code}/start", json={"player_id": guest["player_id"], "token": guest["token"]})
    assert resp.status_code == 403
