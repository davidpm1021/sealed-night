from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import secrets
import string
import threading

from app.models import DeckState, EventPublic, EventRecord, PlayerPublic, PlayerRecord


ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        base = Path(data_dir or os.environ.get("DATA_DIR", "data/events"))
        base.mkdir(parents=True, exist_ok=True)
        self.data_dir = base
        self._events: dict[str, EventRecord] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        for path in self.data_dir.glob("*.json"):
            try:
                event = EventRecord.model_validate_json(path.read_text(encoding="utf-8"))
                self._events[event.code] = event
            except Exception:
                continue

    def _save(self, event: EventRecord) -> None:
        path = self.data_dir / f"{event.code}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(event.model_dump_json(indent=2), encoding="utf-8")
        temp.replace(path)

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(ALPHABET) for _ in range(5))
            if code not in self._events:
                return code

    @staticmethod
    def new_player(name: str) -> PlayerRecord:
        return PlayerRecord(
            id=secrets.token_urlsafe(8),
            name=name.strip(),
            token=secrets.token_urlsafe(24),
            joined_at=utcnow(),
        )

    def create_event(self, *, set_code: str, set_name: str, booster_type: str, host_name: str) -> tuple[EventRecord, PlayerRecord]:
        with self._lock:
            host = self.new_player(host_name)
            event = EventRecord(
                code=self._new_code(),
                set_code=set_code.upper(),
                set_name=set_name,
                booster_type=booster_type,
                host_player_id=host.id,
                created_at=utcnow(),
                players={host.id: host},
            )
            self._events[event.code] = event
            self._save(event)
            return event, host

    def get(self, code: str) -> EventRecord | None:
        with self._lock:
            return self._events.get(code.upper())

    def join(self, code: str, name: str) -> tuple[EventRecord, PlayerRecord]:
        with self._lock:
            event = self._events.get(code.upper())
            if not event:
                raise KeyError("Event not found")
            if event.status != "lobby":
                raise ValueError("This prerelease has already started.")
            player = self.new_player(name)
            event.players[player.id] = player
            self._save(event)
            return event, player

    def authenticate(self, event: EventRecord, player_id: str, token: str) -> PlayerRecord:
        player = event.players.get(player_id)
        if not player or not secrets.compare_digest(player.token, token):
            raise PermissionError("Invalid player session")
        return player

    def save(self, event: EventRecord) -> None:
        with self._lock:
            self._events[event.code] = event
            self._save(event)

    @staticmethod
    def public(event: EventRecord) -> EventPublic:
        players = [
            PlayerPublic(
                id=p.id,
                name=p.name,
                has_kit=p.kit is not None,
                deck_count=p.deck.card_count,
            )
            for p in sorted(event.players.values(), key=lambda x: x.joined_at)
        ]
        return EventPublic(
            code=event.code,
            set_code=event.set_code,
            set_name=event.set_name,
            booster_type=event.booster_type,
            host_player_id=event.host_player_id,
            status=event.status,
            players=players,
        )
