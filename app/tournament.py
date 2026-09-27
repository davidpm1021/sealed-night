from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models import EventRecord, MatchRecord, MatchResult, RoundRecord, StandingPublic
def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Standing:
    player_id: str
    name: str
    match_points: int = 0
    match_wins: int = 0
    match_losses: int = 0
    match_draws: int = 0
    game_wins: int = 0
    game_losses: int = 0
    game_draws: int = 0
    byes: int = 0


def default_round_count(player_count: int) -> int:
    if player_count <= 1:
        return 0
    if player_count <= 3:
        return 2
    if player_count <= 8:
        return 3
    if player_count <= 16:
        return 4
    return 5


def _completed_matches(event: EventRecord):
    for round_record in event.rounds:
        for match in round_record.matches:
            if match.status == "complete":
                yield match


def compute_standings(event: EventRecord) -> list[StandingPublic]:
    rows = {
        player.id: Standing(player_id=player.id, name=player.name)
        for player in event.players.values()
    }
    for match in _completed_matches(event):
        a = rows[match.player_a_id]
        a.game_wins += match.result.games_a
        a.game_losses += match.result.games_b
        a.game_draws += match.result.draws

        if match.player_b_id is None:
            a.match_wins += 1
            a.match_points += 3
            a.byes += 1
            continue

        b = rows[match.player_b_id]
        b.game_wins += match.result.games_b
        b.game_losses += match.result.games_a
        b.game_draws += match.result.draws

        if match.result.games_a > match.result.games_b:
            a.match_wins += 1
            a.match_points += 3
            b.match_losses += 1
        elif match.result.games_b > match.result.games_a:
            b.match_wins += 1
            b.match_points += 3
            a.match_losses += 1
        else:
            a.match_draws += 1
            b.match_draws += 1
            a.match_points += 1
            b.match_points += 1

    ordered = sorted(
        rows.values(),
        key=lambda s: (
            -s.match_points,
            -s.match_wins,
            -(s.game_wins - s.game_losses),
            -s.game_wins,
            s.name.casefold(),
            s.player_id,
        ),
    )
    return [
        StandingPublic(
            rank=index,
            player_id=row.player_id,
            name=row.name,
            match_points=row.match_points,
            match_wins=row.match_wins,
            match_losses=row.match_losses,
            match_draws=row.match_draws,
            game_wins=row.game_wins,
            game_losses=row.game_losses,
            game_draws=row.game_draws,
            byes=row.byes,
        )
        for index, row in enumerate(ordered, start=1)
    ]


def _pairing_history(event: EventRecord) -> set[frozenset[str]]:
    history: set[frozenset[str]] = set()
    for round_record in event.rounds:
        for match in round_record.matches:
            if match.player_b_id is not None:
                history.add(frozenset((match.player_a_id, match.player_b_id)))
    return history


def _first_round_order(event: EventRecord, player_ids: list[str]) -> list[str]:
    def key(player_id: str) -> str:
        return hashlib.sha256(f"{event.code}:{player_id}:round1".encode("utf-8")).hexdigest()
    return sorted(player_ids, key=key)


def _ranked_player_ids(event: EventRecord) -> list[str]:
    active = {p.id for p in event.players.values() if not p.dropped}
    if not event.rounds:
        return _first_round_order(event, list(active))
    return [row.player_id for row in compute_standings(event) if row.player_id in active]


def _choose_bye(event: EventRecord, ranked_ids: list[str]) -> str | None:
    if len(ranked_ids) % 2 == 0:
        return None
    standings = {s.player_id: s for s in compute_standings(event)}
    for player_id in reversed(ranked_ids):
        if standings.get(player_id) is None or standings[player_id].byes == 0:
            return player_id
    return ranked_ids[-1]


def create_next_round(event: EventRecord) -> RoundRecord | None:
    active = [p for p in event.players.values() if not p.dropped]
    if len(active) < 2:
        raise ValueError("At least two active players are required.")

    if event.rounds and not event.rounds[-1].complete:
        raise ValueError("Finish the current round before creating the next one.")

    if event.max_rounds <= 0:
        event.max_rounds = default_round_count(len(active))

    next_number = len(event.rounds) + 1
    if next_number > event.max_rounds:
        event.status = "complete"
        return None

    ranked_ids = _ranked_player_ids(event)
    bye_id = _choose_bye(event, ranked_ids)
    remaining = [player_id for player_id in ranked_ids if player_id != bye_id]
    history = _pairing_history(event)
    matches: list[MatchRecord] = []

    if bye_id is not None:
        matches.append(
            MatchRecord(
                id=secrets.token_urlsafe(6),
                round_number=next_number,
                player_a_id=bye_id,
                player_b_id=None,
                status="complete",
                result=MatchResult(games_a=2, games_b=0, draws=0),
                winner_id=bye_id,
                completed_at=utcnow(),
            )
        )

    while remaining:
        a = remaining.pop(0)
        opponent_index = next(
            (
                index
                for index, candidate in enumerate(remaining)
                if frozenset((a, candidate)) not in history
            ),
            0,
        )
        b = remaining.pop(opponent_index)
        matches.append(
            MatchRecord(
                id=secrets.token_urlsafe(6),
                round_number=next_number,
                player_a_id=a,
                player_b_id=b,
            )
        )

    round_record = RoundRecord(number=next_number, created_at=utcnow(), matches=matches)
    event.rounds.append(round_record)
    event.status = "playing"
    return round_record


def find_match(event: EventRecord, match_id: str) -> MatchRecord | None:
    for round_record in event.rounds:
        for match in round_record.matches:
            if match.id == match_id:
                return match
    return None


def apply_report(
    event: EventRecord,
    match: MatchRecord,
    *,
    reporter_id: str,
    games_won: int,
    games_lost: int,
    draws: int,
) -> None:
    if match.player_b_id is None:
        raise ValueError("A bye does not need a result report.")
    if reporter_id not in {match.player_a_id, match.player_b_id, event.host_player_id}:
        raise PermissionError("Only a player in this match or the event host can report its result.")
    if games_won == 0 and games_lost == 0 and draws == 0:
        raise ValueError("A match result cannot be 0-0-0.")

    if reporter_id == match.player_b_id:
        games_a, games_b = games_lost, games_won
    else:
        games_a, games_b = games_won, games_lost

    match.result = MatchResult(games_a=games_a, games_b=games_b, draws=draws)
    match.status = "complete"
    match.reported_by = reporter_id
    match.completed_at = utcnow()
    if games_a > games_b:
        match.winner_id = match.player_a_id
    elif games_b > games_a:
        match.winner_id = match.player_b_id
    else:
        match.winner_id = None
