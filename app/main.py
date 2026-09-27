from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.models import (
    AuthRequest,
    CreateEventRequest,
    JoinEventRequest,
    ReportMatchRequest,
    SaveDeckRequest,
    SessionResponse,
)
from app.prerelease import generate_kit
from app.providers import build_provider
from app.providers.base import CardProvider
from app.store import EventStore
from app.tournament import apply_report, create_next_round, find_match


class EventHub:
    def __init__(self) -> None:
        self.connections: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, code: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.connections.setdefault(code, set()).add(ws)

    async def disconnect(self, code: str, ws: WebSocket) -> None:
        async with self._lock:
            sockets = self.connections.get(code)
            if sockets:
                sockets.discard(ws)
                if not sockets:
                    self.connections.pop(code, None)

    async def broadcast(self, code: str, payload: dict) -> None:
        async with self._lock:
            sockets = list(self.connections.get(code, set()))
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(code, ws)


def create_app(provider: CardProvider | None = None, store: EventStore | None = None) -> FastAPI:
    provider = provider or build_provider()
    store = store or EventStore()
    hub = EventHub()
    app = FastAPI(title="Prerelease Night", version="0.2.0")

    root = Path(__file__).resolve().parent.parent
    static_dir = root / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def event_or_404(code: str):
        event = store.get(code)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        return event

    def authenticate(event, player_id: str, token: str):
        try:
            return store.authenticate(event, player_id, token)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc))

    def require_host(event, player_id: str, token: str):
        player = authenticate(event, player_id, token)
        if player.id != event.host_player_id:
            raise HTTPException(status_code=403, detail="Only the event host can do that.")
        return player

    def provider_error(exc: Exception):
        message = str(exc) or exc.__class__.__name__
        status = 503 if "mtgjson" in message.lower() or "install" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message)

    async def publish(event) -> dict:
        public = store.public(event)
        await hub.broadcast(event.code, {"type": "event", "event": public.model_dump()})
        return public.model_dump()

    @app.get("/")
    async def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": "0.2.0", "provider": provider.name}

    @app.get("/api/sets/{set_code}")
    async def set_preview(set_code: str):
        try:
            return await asyncio.to_thread(provider.preview_set, set_code)
        except Exception as exc:
            provider_error(exc)

    @app.post("/api/events", response_model=SessionResponse)
    async def create_event(req: CreateEventRequest):
        try:
            preview = await asyncio.to_thread(provider.preview_set, req.set_code)
        except Exception as exc:
            provider_error(exc)
        booster_type = req.booster_type or preview.recommended_booster_type
        if booster_type not in preview.booster_types:
            raise HTTPException(
                status_code=400,
                detail=f"Booster type {booster_type} is not available for {preview.code}.",
            )
        event, host = store.create_event(
            set_code=preview.code,
            set_name=preview.name,
            booster_type=booster_type,
            host_name=req.host_name,
        )
        return SessionResponse(event=store.public(event), player_id=host.id, token=host.token)

    @app.post("/api/events/{code}/join", response_model=SessionResponse)
    async def join_event(code: str, req: JoinEventRequest):
        try:
            event, player = store.join(code, req.player_name)
        except KeyError:
            raise HTTPException(status_code=404, detail="Event not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        await publish(event)
        return SessionResponse(event=store.public(event), player_id=player.id, token=player.token)

    @app.get("/api/events/{code}")
    async def get_event(code: str):
        return store.public(event_or_404(code))

    @app.post("/api/events/{code}/start")
    async def start_event(code: str, auth: AuthRequest):
        event = event_or_404(code)
        require_host(event, auth.player_id, auth.token)
        if event.status != "lobby":
            return store.public(event)
        if len(event.players) < 1:
            raise HTTPException(status_code=400, detail="No players are in the event.")

        try:
            for target in event.players.values():
                if target.kit is None:
                    target.kit = await asyncio.to_thread(
                        generate_kit,
                        provider,
                        event_code=event.code,
                        player_id=target.id,
                        set_code=event.set_code,
                        booster_type=event.booster_type,
                    )
        except Exception as exc:
            provider_error(exc)
        event.status = "deckbuilding"
        store.save(event)
        await publish(event)
        return store.public(event)

    @app.get("/api/events/{code}/players/{player_id}/kit")
    async def get_kit(code: str, player_id: str, token: str):
        event = event_or_404(code)
        player = authenticate(event, player_id, token)
        if player.kit is None:
            raise HTTPException(status_code=409, detail="The prerelease has not started yet.")
        return {"kit": player.kit, "deck": player.deck}

    @app.put("/api/events/{code}/deck")
    async def save_deck(code: str, req: SaveDeckRequest):
        event = event_or_404(code)
        player = authenticate(event, req.player_id, req.token)
        if player.kit is None:
            raise HTTPException(status_code=409, detail="No sealed pool exists yet.")

        owned = {player.kit.promo.copy_id}
        for pack in player.kit.packs:
            owned.update(card.copy_id for card in pack.cards)
        requested = list(dict.fromkeys(req.main_copy_ids))
        invalid = [copy_id for copy_id in requested if copy_id not in owned]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail="Deck contains cards outside this player's sealed pool.",
            )
        basics = {
            name: max(0, int(req.basics.get(name, 0)))
            for name in ["Plains", "Island", "Swamp", "Mountain", "Forest"]
        }
        player.deck.main_copy_ids = requested
        player.deck.basics = basics
        store.save(event)
        await publish(event)
        return {
            "ok": True,
            "card_count": player.deck.card_count,
            "legal": player.deck.legal_for_sealed,
            "deck": player.deck,
        }

    @app.post("/api/events/{code}/tournament/start")
    async def start_tournament(code: str, auth: AuthRequest):
        event = event_or_404(code)
        require_host(event, auth.player_id, auth.token)
        if event.status not in {"deckbuilding", "playing"}:
            raise HTTPException(status_code=409, detail="The event is not ready for tournament play.")
        if event.rounds:
            return store.public(event)
        active = [player for player in event.players.values() if not player.dropped]
        if len(active) < 2:
            raise HTTPException(status_code=409, detail="At least two players are required.")
        illegal = [player.name for player in active if not player.deck.legal_for_sealed]
        if illegal:
            raise HTTPException(
                status_code=409,
                detail="These players do not have legal 40-card decks yet: " + ", ".join(illegal),
            )
        try:
            create_next_round(event)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        store.save(event)
        await publish(event)
        return store.public(event)

    @app.post("/api/events/{code}/tournament/next-round")
    async def next_round(code: str, auth: AuthRequest):
        event = event_or_404(code)
        require_host(event, auth.player_id, auth.token)
        if event.status != "playing":
            raise HTTPException(status_code=409, detail="Tournament play is not active.")
        try:
            round_record = create_next_round(event)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if round_record is None:
            event.status = "complete"
        store.save(event)
        await publish(event)
        return store.public(event)

    @app.post("/api/events/{code}/matches/{match_id}/report")
    async def report_match(code: str, match_id: str, req: ReportMatchRequest):
        event = event_or_404(code)
        reporter = authenticate(event, req.player_id, req.token)
        match = find_match(event, match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found.")
        try:
            apply_report(
                event,
                match,
                reporter_id=reporter.id,
                games_won=req.games_won,
                games_lost=req.games_lost,
                draws=req.draws,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        store.save(event)
        await publish(event)
        return {"ok": True, "match": match, "event": store.public(event)}

    @app.websocket("/ws/events/{code}")
    async def event_ws(code: str, ws: WebSocket):
        event = store.get(code)
        if not event:
            await ws.close(code=4404)
            return
        code = event.code
        await hub.connect(code, ws)
        await ws.send_json({"type": "event", "event": store.public(event).model_dump()})
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            await hub.disconnect(code, ws)
        except Exception:
            await hub.disconnect(code, ws)

    return app


app = create_app()
