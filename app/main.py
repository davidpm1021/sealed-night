from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.models import (
    AuthRequest,
    CreateEventRequest,
    GameActionRequest,
    GamePassRequest,
    GameResultRequest,
    GameSessionRequest,
    JoinEventRequest,
    ReportMatchRequest,
    SaveDeckRequest,
    SessionResponse,
)
from app.engine.service import GameEngineManager
from app.prerelease import generate_kit
from app.providers import build_provider
from app.providers.base import CardProvider
from app.store import EventStore
from app.tournament import apply_report, create_next_round, find_match, record_engine_game


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


def create_app(
    provider: CardProvider | None = None,
    store: EventStore | None = None,
    engine_manager: GameEngineManager | None = None,
) -> FastAPI:
    provider = provider or build_provider()
    store = store or EventStore()
    engine_manager = engine_manager or GameEngineManager()
    hub = EventHub()
    kit_locks = defaultdict(asyncio.Lock)
    launch_locks = defaultdict(asyncio.Lock)
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
        if kit_locks[code.upper()].locked():
            raise HTTPException(409, "Kits are being generated; joining is closed.")
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

    @app.post('/api/events/{code}/resume', response_model=SessionResponse)
    async def resume_session(code: str, auth: AuthRequest):
        event = event_or_404(code)
        player = authenticate(event, auth.player_id, auth.token)
        return SessionResponse(event=store.public(event), player_id=player.id, token=player.token)

    @app.post("/api/events/{code}/start")
    async def start_event(code: str, auth: AuthRequest):
        event = event_or_404(code)
        require_host(event, auth.player_id, auth.token)
        async with kit_locks[event.code]:
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
                        store.save(event)
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

        for round_record in event.rounds:
            for match in round_record.matches:
                if player.id in {match.player_a_id, match.player_b_id} and (
                    launch_locks[match.id].locked() or
                    (match.status != 'complete' and match.engine_game_id and match.engine_status in {'idle', 'active'})
                ):
                    raise HTTPException(409, 'Finish the current game before sideboarding.')
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
        if launch_locks[match.id].locked():
            raise HTTPException(409, 'A game is starting. Please wait.')
        if match.engine_game_id and match.engine_status in {'idle', 'active'}:
            raise HTTPException(409, 'Finish the game or ask the host to mark it interrupted before reporting.')
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

    @app.get("/api/engine/status")
    async def engine_status():
        return await asyncio.to_thread(engine_manager.status)

    @app.post("/api/events/{code}/matches/{match_id}/game/start")
    async def start_rules_game(code: str, match_id: str, auth: AuthRequest):
        event = event_or_404(code)
        player = authenticate(event, auth.player_id, auth.token)
        match = find_match(event, match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found.")
        if player.id not in {match.player_a_id, match.player_b_id, event.host_player_id}:
            raise HTTPException(status_code=403, detail="Only a player in this match or the host can launch it.")
        if match.status == "complete":
            raise HTTPException(status_code=409, detail="This match is already complete.")
        async with launch_locks[match.id]:
            if match.status == 'complete':
                raise HTTPException(409, 'This match is already complete.')
            try:
                game = await asyncio.to_thread(engine_manager.start_match, event, match)
            except (ValueError, KeyError, PermissionError) as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Rules engine could not start: {exc}")
            store.save(event)
            await publish(event)
            return {"ok": True, "table_id": game.runtime.table_id, "match_id": match.id}

    async def game_snapshot(event, match, player):
        if match.engine_status == 'interrupted':
            raise ValueError('This game was marked interrupted. Return to pairings to start a replacement.')
        if hasattr(engine_manager, 'resume'):
            await asyncio.to_thread(engine_manager.resume, match)
        snapshot = await asyncio.to_thread(engine_manager.snapshot, match.id, player.id)
        if match.engine_status == 'interrupted' or snapshot.get('game_id', match.engine_game_id) != match.engine_game_id:
            raise ValueError('This game changed or was interrupted. Return to pairings.')
        if snapshot.get('game_id') == match.engine_game_id and snapshot.get('game_over'):
            changed = match.engine_status != 'finished'
            match.engine_status = 'finished'
            if snapshot.get('result_known'):
                changed |= record_engine_game(event, match, snapshot['game_id'], snapshot.get('winner_id'))
            if changed:
                store.save(event)
                await publish(event)
        snapshot['match'] = next(m.model_dump() for r in store.public(event).rounds for m in r.matches if m.id == match.id)
        snapshot['result_recorded'] = match.engine_game_id in match.engine_results
        return snapshot

    async def require_live_game(event, match, player):
        if match.status == 'complete':
            raise ValueError('This match is already complete.')
        snapshot = await game_snapshot(event, match, player)
        if snapshot.get('game_over'):
            raise ValueError('This game has ended. Return to pairings.')

    @app.post('/api/events/{code}/matches/{match_id}/game/result')
    async def confirm_game_result(code: str, match_id: str, req: GameResultRequest):
        event = event_or_404(code)
        player = authenticate(event, req.player_id, req.token)
        match = find_match(event, match_id)
        if match is None:
            raise HTTPException(404, 'Match not found.')
        if player.id not in {match.player_a_id, match.player_b_id, event.host_player_id}:
            raise HTTPException(403, 'Only the players or host can confirm a result.')
        if req.game_id != match.engine_game_id or match.engine_status != 'finished':
            raise HTTPException(409, 'The game must finish before confirming its result.')
        if req.winner_id not in {match.player_a_id, match.player_b_id, None}:
            raise HTTPException(400, 'Winner must be seated in the match.')
        if req.game_id in match.engine_results:
            if match.engine_results[req.game_id] != (req.winner_id or 'draw'):
                raise HTTPException(409, 'That game result is already recorded.')
        elif match.status == 'complete':
            raise HTTPException(409, 'The match is already complete.')
        else:
            match.game_reports[player.id] = req.winner_id or 'draw'
            agreed = (match.game_reports.get(match.player_a_id) == match.game_reports.get(match.player_b_id)
                      and match.player_a_id in match.game_reports)
            if agreed or player.id == event.host_player_id:
                record_engine_game(event, match, req.game_id, req.winner_id)
            store.save(event)
            await publish(event)
        return {'event': store.public(event)}

    @app.post('/api/events/{code}/matches/{match_id}/game/interrupt')
    async def interrupt_game(code: str, match_id: str, req: GameSessionRequest):
        event = event_or_404(code)
        require_host(event, req.player_id, req.token)
        match = find_match(event, match_id)
        if match is None:
            raise HTTPException(404, 'Match not found.')
        if launch_locks[match.id].locked():
            raise HTTPException(409, 'A game is starting. Please wait.')
        if match.status == 'complete' or req.game_id != match.engine_game_id or req.game_id in match.engine_results:
            raise HTTPException(409, 'This game cannot be interrupted.')
        match.engine_status = 'interrupted'
        match.game_reports.clear()
        store.save(event)
        if hasattr(engine_manager, 'release'):
            await asyncio.to_thread(engine_manager.release, match.id)
        await publish(event)
        return {'event': store.public(event)}

    @app.get("/api/events/{code}/matches/{match_id}/game")
    async def get_rules_game(code: str, match_id: str, player_id: str, token: str):
        event = event_or_404(code)
        player = authenticate(event, player_id, token)
        match = find_match(event, match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found.")
        if player.id not in {match.player_a_id, match.player_b_id}:
            raise HTTPException(status_code=403, detail="Only a seated player can view this game.")
        try:
            return await game_snapshot(event, match, player)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Rules engine unavailable: {exc}")

    @app.post("/api/events/{code}/matches/{match_id}/game/action")
    async def rules_game_action(code: str, match_id: str, req: GameActionRequest):
        event = event_or_404(code)
        player = authenticate(event, req.player_id, req.token)
        match = find_match(event, match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found.")
        if player.id not in {match.player_a_id, match.player_b_id}:
            raise HTTPException(status_code=403, detail="Only a seated player can act in this game.")
        arguments = req.model_dump(exclude={"player_id", "token"})
        try:
            await require_live_game(event, match, player)
            result = await asyncio.to_thread(engine_manager.choose_action, match.id, player.id, arguments)
            await game_snapshot(event, match, player)
            return result
        except (KeyError, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Rules engine action failed: {exc}")

    @app.post("/api/events/{code}/matches/{match_id}/game/pass")
    async def rules_game_pass(code: str, match_id: str, req: GamePassRequest):
        event = event_or_404(code)
        player = authenticate(event, req.player_id, req.token)
        match = find_match(event, match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found.")
        if player.id not in {match.player_a_id, match.player_b_id}:
            raise HTTPException(status_code=403, detail="Only a seated player can act in this game.")
        try:
            await require_live_game(event, match, player)
            result = await asyncio.to_thread(
                engine_manager.pass_priority,
                match.id,
                player.id,
                until=req.until,
                board_cursor=req.board_cursor,
                **({'game_id': req.game_id, 'decision_id': req.decision_id} if req.game_id else {}),
            )
            await game_snapshot(event, match, player)
            return result
        except (KeyError, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Rules engine pass failed: {exc}")

    @app.post("/api/events/{code}/matches/{match_id}/game/concede")
    async def rules_game_concede(code: str, match_id: str, auth: GameSessionRequest):
        event = event_or_404(code)
        player = authenticate(event, auth.player_id, auth.token)
        match = find_match(event, match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found.")
        if player.id not in {match.player_a_id, match.player_b_id}:
            raise HTTPException(status_code=403, detail="Only a seated player can concede this game.")
        try:
            await require_live_game(event, match, player)
            result = await asyncio.to_thread(engine_manager.concede, match.id, player.id, game_id=auth.game_id)
            await game_snapshot(event, match, player)
            return result
        except (KeyError, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Rules engine concede failed: {exc}")

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
