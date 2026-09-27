from app.models import EventRecord, PlayerRecord
from app.tournament import apply_report, compute_standings, create_next_round, default_round_count


def player(pid: str, name: str) -> PlayerRecord:
    return PlayerRecord(id=pid, name=name, token=f"token-{pid}", joined_at=f"2026-01-01T00:00:0{pid[-1]}+00:00")


def event_with_players(count: int) -> EventRecord:
    players = {f"p{i}": player(f"p{i}", f"Player {i}") for i in range(1, count + 1)}
    return EventRecord(
        code="ABCDE",
        set_code="DEMO",
        set_name="Demo",
        booster_type="play",
        host_player_id="p1",
        created_at="2026-01-01T00:00:00+00:00",
        players=players,
    )


def test_default_round_counts():
    assert default_round_count(2) == 2
    assert default_round_count(4) == 3
    assert default_round_count(8) == 3
    assert default_round_count(9) == 4
    assert default_round_count(16) == 4


def test_odd_player_round_has_one_completed_bye():
    event = event_with_players(5)
    round_one = create_next_round(event)
    assert round_one is not None
    byes = [match for match in round_one.matches if match.player_b_id is None]
    assert len(byes) == 1
    assert byes[0].status == "complete"
    assert byes[0].result.games_a == 2
    assert byes[0].winner_id == byes[0].player_a_id


def test_report_is_oriented_to_player_a_and_updates_standings():
    event = event_with_players(2)
    round_one = create_next_round(event)
    match = next(match for match in round_one.matches if match.player_b_id is not None)

    # Report from player B's perspective: B won 2-1.
    apply_report(
        event,
        match,
        reporter_id=match.player_b_id,
        games_won=2,
        games_lost=1,
        draws=0,
    )
    assert match.result.games_a == 1
    assert match.result.games_b == 2
    assert match.winner_id == match.player_b_id

    standings = compute_standings(event)
    assert standings[0].player_id == match.player_b_id
    assert standings[0].match_points == 3
    assert standings[0].match_wins == 1


def test_second_round_avoids_repeat_when_possible():
    event = event_with_players(4)
    first = create_next_round(event)
    first_pairs = {
        frozenset((m.player_a_id, m.player_b_id))
        for m in first.matches
        if m.player_b_id is not None
    }
    for match in first.matches:
        apply_report(
            event,
            match,
            reporter_id=match.player_a_id,
            games_won=2,
            games_lost=0,
            draws=0,
        )

    second = create_next_round(event)
    second_pairs = {
        frozenset((m.player_a_id, m.player_b_id))
        for m in second.matches
        if m.player_b_id is not None
    }
    assert first_pairs.isdisjoint(second_pairs)
