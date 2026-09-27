from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import threading
from typing import Any

from app.engine.deck_export import write_xmage_deck
from app.engine.magebench_mcp import BridgeMcpClient
from app.engine.magebench_runtime import MageBenchRuntime, MatchRuntime
from app.models import EventRecord, MatchRecord


@dataclass
class ManagedGame:
    match_id: str
    runtime: MatchRuntime
    player_bridges: dict[str, BridgeMcpClient]


class GameEngineManager:
    """Coordinates browser match sessions with the local XMage runtime."""

    def __init__(
        self,
        *,
        runtime: MageBenchRuntime | None = None,
        repo_root: str | Path | None = None,
        work_root: str | Path | None = None,
    ) -> None:
        self.runtime = runtime or MageBenchRuntime(
            repo_root=repo_root or os.environ.get("MAGE_BENCH_ROOT", ".vendor/mage-bench"),
            work_root=work_root or os.environ.get("XMAGE_WORK_ROOT", "data/xmage"),
        )
        self.games: dict[str, ManagedGame] = {}
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        try:
            self.runtime.validate_installation()
            installed = True
            error = None
        except Exception as exc:
            installed = False
            error = str(exc)
        return {
            "installed": installed,
            "running": self.runtime.running,
            "active_matches": list(self.games),
            "error": error,
        }

    def start_match(self, event: EventRecord, match: MatchRecord) -> ManagedGame:
        if match.player_b_id is None:
            raise ValueError("A bye has no game to launch.")
        with self._lock:
            existing = self.games.get(match.id)
            if existing is not None:
                return existing

            player_a = event.players.get(match.player_a_id)
            player_b = event.players.get(match.player_b_id)
            if player_a is None or player_b is None:
                raise ValueError("Match references a player who is no longer in the event.")
            if not player_a.deck.legal_for_sealed or not player_b.deck.legal_for_sealed:
                raise ValueError("Both players need legal 40-card decks before launching the match.")

            self.runtime.start()
            deck_dir = self.runtime.work_root / "decks" / event.code / f"round-{match.round_number}"
            deck_a = write_xmage_deck(
                player_a,
                deck_dir / f"{match.id}-a.dck",
                name=f"{player_a.name} R{match.round_number}",
            )
            deck_b = write_xmage_deck(
                player_b,
                deck_dir / f"{match.id}-b.dck",
                name=f"{player_b.name} R{match.round_number}",
            )
            runtime_match = self.runtime.start_match(
                match_key=f"{event.code}-r{match.round_number}-{match.id}",
                player_a_id=player_a.id,
                player_b_id=player_b.id,
                deck_a=deck_a,
                deck_b=deck_b,
            )
            game = ManagedGame(
                match_id=match.id,
                runtime=runtime_match,
                player_bridges={
                    player_a.id: runtime_match.player_a.client,
                    player_b.id: runtime_match.player_b.client,
                },
            )
            self.games[match.id] = game
            match.engine_game_id = runtime_match.table_id
            return game

    def _bridge(self, match_id: str, player_id: str) -> BridgeMcpClient:
        game = self.games.get(match_id)
        if game is None:
            raise KeyError("This rules-engine match is not running.")
        bridge = game.player_bridges.get(player_id)
        if bridge is None:
            raise PermissionError("This player is not seated in the match.")
        return bridge

    def snapshot(self, match_id: str, player_id: str) -> dict[str, Any]:
        bridge = self._bridge(match_id, player_id)
        return {
            "state": bridge.get_game_state(),
            "action": bridge.get_action_choices(),
        }

    def choose_action(self, match_id: str, player_id: str, arguments: dict[str, Any]) -> Any:
        clean = {key: value for key, value in arguments.items() if value is not None}
        return self._bridge(match_id, player_id).choose_action(**clean)

    def pass_priority(
        self,
        match_id: str,
        player_id: str,
        *,
        until: str | None = None,
        board_cursor: int | None = None,
    ) -> Any:
        args: dict[str, Any] = {}
        if until is not None:
            args["until"] = until
        if board_cursor is not None:
            args["board_cursor"] = board_cursor
        return self._bridge(match_id, player_id).pass_priority(**args)

    def concede(self, match_id: str, player_id: str) -> Any:
        return self._bridge(match_id, player_id).concede()

    def stop(self) -> None:
        with self._lock:
            self.games.clear()
            self.runtime.stop()
