from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from types import SimpleNamespace
from typing import Any

from app.engine.deck_export import write_xmage_deck
from app.engine.magebench_mcp import BridgeMcpClient
from app.engine.magebench_runtime import MageBenchRuntime, MatchRuntime
from app.models import EventRecord, MatchRecord


def decision_id(action: dict) -> str:
    stable = {k: v for k, v in action.items() if k not in {'recent_chat', 'board', 'board_delta'}}
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


@dataclass
class ManagedGame:
    match_id: str
    runtime: MatchRuntime
    player_bridges: dict[str, BridgeMcpClient]
    restored: bool = False
    ended: bool = False
    winner_id: str | None = None
    result_known: bool = False
    commands: dict[str, threading.Lock] = field(default_factory=dict)


class GameEngineManager:
    """Adapter boundary: all bridge wire formats and recovery live here."""

    def __init__(self, *, runtime=None, repo_root=None, work_root=None):
        self.runtime = runtime or MageBenchRuntime(
            repo_root=repo_root or os.environ.get('MAGE_BENCH_ROOT', '.vendor/mage-bench'),
            work_root=work_root or os.environ.get('XMAGE_WORK_ROOT', 'data/xmage'),
        )
        self.games: dict[str, ManagedGame] = {}
        self._lock = threading.RLock()

    def status(self):
        try:
            self.runtime.validate_installation()
            error = None
        except Exception as exc:
            error = str(exc)
        return {'installed': error is None, 'running': self.runtime.running,
                'active_matches': list(self.games), 'error': error}

    def _manifest(self, match_id):
        # Hash identifiers so this private local filename cannot traverse directories.
        key = hashlib.sha256(match_id.encode()).hexdigest()
        return self.runtime.work_root / 'sessions' / f'{key}.json'

    def _save_manifest(self, game):
        if self.games.get(game.match_id) is not game:
            return
        path = self._manifest(game.match_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {'table_id': game.runtime.table_id, 'ended': game.ended,
                'winner_id': game.winner_id, 'result_known': game.result_known,
                'players': {pid: {'endpoint': bridge.endpoint,
                                 'username': seat.username}
                            for (pid, bridge), seat in zip(game.player_bridges.items(),
                                                         [game.runtime.player_a, game.runtime.player_b])}}
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(data), encoding='utf-8')
        temp.replace(path)

    def resume(self, match: MatchRecord):
        with self._lock:
            if match.id in self.games:
                return self.games[match.id]
            if not match.engine_game_id:
                raise KeyError('No game has been launched yet.')
            path = self._manifest(match.id)
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if data['table_id'] != match.engine_game_id:
                    raise ValueError('Saved table differs from the event.')
                players = data['players']
                if set(players) != {match.player_a_id, match.player_b_id}:
                    raise ValueError('Saved seats differ from the match.')
                bridges = {}
                for pid, seat in players.items():
                    from urllib.parse import urlparse
                    endpoint = urlparse(seat['endpoint'])
                    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or endpoint.path != '/mcp':
                        raise ValueError('Invalid local bridge address.')
                    bridge = BridgeMcpClient(seat['endpoint'], timeout=5)
                    if not data.get('ended'):
                        state = bridge.get_game_state()
                        if not any(p.get('is_you') and p.get('name') == seat['username']
                                   for p in state.get('players', [])):
                            raise ValueError('Cannot verify the original player bridge.')
                    bridges[pid] = bridge
                runtime = SimpleNamespace(table_id=data['table_id'],
                    player_a=SimpleNamespace(username=players[match.player_a_id]['username']),
                    player_b=SimpleNamespace(username=players[match.player_b_id]['username']))
                game = ManagedGame(match.id, runtime, bridges, restored=True,
                                   ended=data.get('ended', False), winner_id=data.get('winner_id'),
                                   result_known=data.get('result_known', False))
                self.games[match.id] = game
                return game
            except Exception as exc:
                raise KeyError('The original game could not be resumed. Retry if the engine is reconnecting; '
                               'otherwise ask the host to mark this game interrupted. Saved pools and scores are safe.') from exc

    def start_match(self, event, match):
        if match.player_b_id is None or match.status == 'complete':
            raise ValueError('This match cannot launch a game.')
        with self._lock:
            if match.engine_game_id and match.engine_game_id not in match.engine_results and match.engine_status != 'interrupted':
                return self.resume(match)
            if event.set_code == 'DEMO':
                raise ValueError('Demo cards are fictional. Use manual match results, or create a real-set event for XMage.')
            a, b = event.players[match.player_a_id], event.players[match.player_b_id]
            if not a.deck.legal_for_sealed or not b.deck.legal_for_sealed:
                raise ValueError('Both players need legal 40-card decks.')
            self.release(match.id)
            self.runtime.start()
            key = f'{event.code}-r{match.round_number}-{match.id}-{secrets.token_hex(4)}'
            deck_dir = self.runtime.work_root / 'decks' / key
            deck_a = write_xmage_deck(a, deck_dir / 'a.dck', name=a.name)
            deck_b = write_xmage_deck(b, deck_dir / 'b.dck', name=b.name)
            table = self.runtime.start_match(match_key=key, player_a_id=a.id, player_b_id=b.id,
                                             deck_a=deck_a, deck_b=deck_b)
            game = ManagedGame(match.id, table, {a.id: table.player_a.client, b.id: table.player_b.client})
            self.games[match.id] = game
            self._save_manifest(game)
            match.engine_game_id = table.table_id
            match.engine_status = 'active'
            match.game_reports.clear()
            return game

    def _game_for_player(self, match_id, player_id):
        with self._lock:
            game = self.games.get(match_id)
            if game is None:
                raise KeyError('This rules-engine game is not running.')
            if player_id not in game.player_bridges:
                raise PermissionError('This player is not seated in the match.')
            return game, game.player_bridges[player_id]

    def _terminal(self, game, result):
        if isinstance(result, dict) and result.get('game_over') is True:
            with self._lock:
                game.ended = True
                self._save_manifest(game)

    def snapshot(self, match_id, player_id):
        game, bridge = self._game_for_player(match_id, player_id)
        if game.ended:
            state, action = {}, {'game_over': True, 'action_pending': False}
        else:
            state, action = bridge.get_game_state(), bridge.get_action_choices()
            self._terminal(game, action)
        return {'state': state, 'action': action, 'game_id': game.runtime.table_id,
                'decision_id': decision_id(action), 'game_over': game.ended,
                'result_known': game.result_known, 'winner_id': game.winner_id}

    def _command(self, match_id, player_id, operation, *, game_id=None, expected_decision=None):
        game, bridge = self._game_for_player(match_id, player_id)
        lock = game.commands.setdefault(player_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ValueError('An action is already resolving. Refresh before acting again.')
        try:
            if game.ended or (game_id and game_id != game.runtime.table_id):
                raise ValueError('This game has ended or changed. Refresh the game.')
            if expected_decision and decision_id(bridge.get_action_choices()) != expected_decision:
                raise ValueError('That decision changed. Refresh before choosing again.')
            result = operation(bridge)
            if isinstance(result, dict) and (result.get('error') or result.get('success') is False):
                raise ValueError(result.get('error') or 'The engine rejected this action.')
            if self.games.get(match_id) is not game:
                raise ValueError('This game was interrupted while the action resolved. Refresh pairings.')
            self._terminal(game, result)
            return result
        finally:
            lock.release()

    def choose_action(self, match_id, player_id, arguments):
        args = {k: v for k, v in arguments.items() if v is not None}
        game_id, expected = args.pop('game_id', None), args.pop('decision_id', None)
        return self._command(match_id, player_id, lambda b: b.choose_action(**args),
                             game_id=game_id, expected_decision=expected)

    def pass_priority(self, match_id, player_id, *, until=None, board_cursor=None,
                      game_id=None, decision_id=None):
        args = {k: v for k, v in {'until': until, 'board_cursor': board_cursor}.items() if v is not None}
        return self._command(match_id, player_id, lambda b: b.pass_priority(**args),
                             game_id=game_id, expected_decision=decision_id)

    def concede(self, match_id, player_id, *, game_id=None):
        game, _ = self._game_for_player(match_id, player_id)
        result = self._command(match_id, player_id, lambda b: b.concede(), game_id=game_id)
        # Only an explicit engine acknowledgment can establish a concession result.
        if isinstance(result, dict) and result.get('success') is True:
            with self._lock:
                if self.games.get(match_id) is not game or game.result_known:
                    return result
                game.ended = True
                game.result_known = True
                game.winner_id = next(pid for pid in game.player_bridges if pid != player_id)
                self._save_manifest(game)
        return result

    def release(self, match_id):
        with self._lock:
            game = self.games.pop(match_id, None)
            if game and not game.restored:
                self.runtime.stop_match(game.runtime)

    def stop(self):
        with self._lock:
            self.games.clear()
            self.runtime.stop()
