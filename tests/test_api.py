from pathlib import Path
from types import SimpleNamespace
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


def _save_legal_demo_deck(client: TestClient, code: str, session: dict):
    kit = client.get(
        f"/api/events/{code}/players/{session['player_id']}/kit",
        params={"token": session["token"]},
    ).json()["kit"]
    pool_ids = [kit["promo"]["copy_id"]] + [
        card["copy_id"] for pack in kit["packs"] for card in pack["cards"]
    ]
    response = client.put(
        f"/api/events/{code}/deck",
        json={
            "player_id": session["player_id"],
            "token": session["token"],
            "main_copy_ids": pool_ids[:23],
            "basics": {"Plains": 9, "Island": 8},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["legal"] is True


def test_four_player_tournament_round_and_reporting(tmp_path: Path):
    app = create_app(DemoProvider(), EventStore(tmp_path / "events"))
    client = TestClient(app)

    host = client.post("/api/events", json={"host_name": "Host", "set_code": "DEMO"}).json()
    code = host["event"]["code"]
    sessions = [host]
    for name in ["A", "B", "C"]:
        joined = client.post(f"/api/events/{code}/join", json={"player_name": name})
        assert joined.status_code == 200
        sessions.append(joined.json())

    start = client.post(
        f"/api/events/{code}/start",
        json={"player_id": host["player_id"], "token": host["token"]},
    )
    assert start.status_code == 200
    for session in sessions:
        _save_legal_demo_deck(client, code, session)

    tournament = client.post(
        f"/api/events/{code}/tournament/start",
        json={"player_id": host["player_id"], "token": host["token"]},
    )
    assert tournament.status_code == 200, tournament.text
    event = tournament.json()
    assert event["status"] == "playing"
    assert event["current_round"] == 1
    assert event["max_rounds"] == 3
    assert len(event["rounds"][0]["matches"]) == 2

    by_player = {session["player_id"]: session for session in sessions}
    for match in event["rounds"][0]["matches"]:
        reporter = by_player[match["player_a_id"]]
        result = client.post(
            f"/api/events/{code}/matches/{match['id']}/report",
            json={
                "player_id": reporter["player_id"],
                "token": reporter["token"],
                "games_won": 2,
                "games_lost": 0,
                "draws": 0,
            },
        )
        assert result.status_code == 200, result.text

    after = client.get(f"/api/events/{code}").json()
    assert after["rounds"][0]["complete"] is True
    assert sum(1 for row in after["standings"] if row["match_points"] == 3) == 2

    next_round = client.post(
        f"/api/events/{code}/tournament/next-round",
        json={"player_id": host["player_id"], "token": host["token"]},
    )
    assert next_round.status_code == 200, next_round.text
    assert next_round.json()["current_round"] == 2


class FakeEngineManager:
    def __init__(self):
        self.games = {}

    def status(self):
        return {"installed": True, "running": True, "active_matches": list(self.games), "error": None}

    def start_match(self, event, match):
        match.engine_game_id = f"table-{match.id}"
        game = SimpleNamespace(runtime=SimpleNamespace(table_id=match.engine_game_id))
        self.games[match.id] = game
        return game

    def snapshot(self, match_id, player_id):
        if match_id not in self.games:
            raise KeyError("not running")
        return {
            "state": {
                "available": True,
                "turn": 1,
                "phase": "PRECOMBAT_MAIN",
                "active_player": player_id,
                "players": [{"name": player_id, "life": 20, "is_you": True, "hand": []}],
                "stack": [],
                "combat": [],
            },
            "action": {"action_pending": True, "response_type": "select", "choices": []},
        }

    def choose_action(self, match_id, player_id, arguments):
        return {"success": True, "player_id": player_id, "arguments": arguments}

    def pass_priority(self, match_id, player_id, *, until=None, board_cursor=None):
        return {"action_pending": False, "until": until, "board_cursor": board_cursor}

    def concede(self, match_id, player_id):
        return {"success": True, "conceded": player_id}


def test_rules_engine_api_keeps_game_bound_to_seated_players(tmp_path: Path):
    engine = FakeEngineManager()
    app = create_app(DemoProvider(), EventStore(tmp_path / "events"), engine_manager=engine)
    client = TestClient(app)

    host = client.post("/api/events", json={"host_name": "Host", "set_code": "DEMO"}).json()
    code = host["event"]["code"]
    guest = client.post(f"/api/events/{code}/join", json={"player_name": "Guest"}).json()
    outsider = client.post(f"/api/events/{code}/join", json={"player_name": "Outsider"}).json()

    started = client.post(
        f"/api/events/{code}/start",
        json={"player_id": host["player_id"], "token": host["token"]},
    )
    assert started.status_code == 200
    for session in [host, guest, outsider]:
        _save_legal_demo_deck(client, code, session)

    tournament = client.post(
        f"/api/events/{code}/tournament/start",
        json={"player_id": host["player_id"], "token": host["token"]},
    ).json()
    match = next(m for m in tournament["rounds"][0]["matches"] if m["player_b_id"] is not None)
    sessions = {s["player_id"]: s for s in [host, guest, outsider]}
    seated = sessions[match["player_a_id"]]

    launched = client.post(
        f"/api/events/{code}/matches/{match['id']}/game/start",
        json={"player_id": seated["player_id"], "token": seated["token"]},
    )
    assert launched.status_code == 200, launched.text
    assert launched.json()["table_id"].startswith("table-")

    snapshot = client.get(
        f"/api/events/{code}/matches/{match['id']}/game",
        params={"player_id": seated["player_id"], "token": seated["token"]},
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["state"]["players"][0]["is_you"] is True

    outsider_session = next(
        s for s in [host, guest, outsider]
        if s["player_id"] not in {match["player_a_id"], match["player_b_id"]}
    )
    forbidden = client.get(
        f"/api/events/{code}/matches/{match['id']}/game",
        params={"player_id": outsider_session["player_id"], "token": outsider_session["token"]},
    )
    assert forbidden.status_code == 403
