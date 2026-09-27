from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class CardData(BaseModel):
    id: str
    name: str
    rarity: str = "common"
    mana_cost: str | None = None
    mana_value: float | None = None
    colors: list[str] = Field(default_factory=list)
    color_identity: list[str] = Field(default_factory=list)
    type_line: str = ""
    oracle_text: str | None = None
    set_code: str = ""
    number: str = ""
    foil: bool = False
    scryfall_id: str | None = None
    image_url: str | None = None


class CardInstance(CardData):
    copy_id: str
    source: str


class PackData(BaseModel):
    number: int
    cards: list[CardInstance]


class PrereleaseKit(BaseModel):
    set_code: str
    booster_type: str
    promo: CardInstance
    packs: list[PackData]
    generated_at: str
    product_note: str = "Simulated six-booster kit; physical product contents may differ."


class DeckState(BaseModel):
    main_copy_ids: list[str] = Field(default_factory=list)
    basics: dict[str, int] = Field(
        default_factory=lambda: {
            "Plains": 0,
            "Island": 0,
            "Swamp": 0,
            "Mountain": 0,
            "Forest": 0,
        }
    )

    @property
    def card_count(self) -> int:
        return len(self.main_copy_ids) + sum(max(0, int(v)) for v in self.basics.values())

    @property
    def legal_for_sealed(self) -> bool:
        return self.card_count >= 40


class MatchResult(BaseModel):
    games_a: int = Field(default=0, ge=0, le=9)
    games_b: int = Field(default=0, ge=0, le=9)
    draws: int = Field(default=0, ge=0, le=9)


class MatchRecord(BaseModel):
    id: str
    round_number: int
    player_a_id: str
    player_b_id: str | None = None
    status: Literal["pending", "complete"] = "pending"
    result: MatchResult = Field(default_factory=MatchResult)
    winner_id: str | None = None
    reported_by: str | None = None
    completed_at: str | None = None
    engine_game_id: str | None = None
    engine_status: Literal["idle", "active", "finished", "interrupted"] = "idle"
    engine_results: dict[str, str] = Field(default_factory=dict)
    game_reports: dict[str, str] = Field(default_factory=dict)

    @property
    def is_bye(self) -> bool:
        return self.player_b_id is None


class RoundRecord(BaseModel):
    number: int
    created_at: str
    matches: list[MatchRecord] = Field(default_factory=list)

    @property
    def complete(self) -> bool:
        return all(match.status == "complete" for match in self.matches)


class PlayerRecord(BaseModel):
    id: str
    name: str
    token: str
    joined_at: str
    kit: PrereleaseKit | None = None
    deck: DeckState = Field(default_factory=DeckState)
    dropped: bool = False


class EventRecord(BaseModel):
    code: str
    set_code: str
    set_name: str
    booster_type: str
    host_player_id: str
    status: Literal["lobby", "deckbuilding", "playing", "complete"] = "lobby"
    created_at: str
    players: dict[str, PlayerRecord] = Field(default_factory=dict)
    rounds: list[RoundRecord] = Field(default_factory=list)
    max_rounds: int = 0


class CreateEventRequest(BaseModel):
    host_name: str = Field(min_length=1, max_length=40)
    set_code: str = Field(default="FRA", min_length=2, max_length=8)
    booster_type: str | None = None


class JoinEventRequest(BaseModel):
    player_name: str = Field(min_length=1, max_length=40)


class AuthRequest(BaseModel):
    player_id: str
    token: str


class SaveDeckRequest(AuthRequest):
    main_copy_ids: list[str] = Field(default_factory=list)
    basics: dict[str, int] = Field(default_factory=dict)


class ReportMatchRequest(AuthRequest):
    games_won: int = Field(ge=0, le=9)
    games_lost: int = Field(ge=0, le=9)
    draws: int = Field(default=0, ge=0, le=9)


class GameActionRequest(AuthRequest):
    game_id: str
    decision_id: str
    choice: str | None = None
    amount: int | None = None
    amounts: list[int] | None = None
    pile: int | None = None
    text: str | None = None
    mana_plan: str | None = None
    auto_tap: bool | None = None
    attackers: str | None = None
    blockers: str | None = None


class GamePassRequest(AuthRequest):
    game_id: str
    decision_id: str
    until: str | None = None
    board_cursor: int | None = None


class GameResultRequest(AuthRequest):
    game_id: str
    winner_id: str | None = None


class GameSessionRequest(AuthRequest):
    game_id: str


class PlayerPublic(BaseModel):
    id: str
    name: str
    has_kit: bool
    deck_count: int
    deck_legal: bool
    dropped: bool = False


class MatchPublic(BaseModel):
    id: str
    round_number: int
    player_a_id: str
    player_b_id: str | None
    status: str
    games_a: int
    games_b: int
    draws: int
    winner_id: str | None
    engine_game_id: str | None = None
    engine_status: str = "idle"
    game_reports: dict[str, str] = Field(default_factory=dict)


class RoundPublic(BaseModel):
    number: int
    complete: bool
    matches: list[MatchPublic]


class StandingPublic(BaseModel):
    rank: int
    player_id: str
    name: str
    match_points: int
    match_wins: int
    match_losses: int
    match_draws: int
    game_wins: int
    game_losses: int
    game_draws: int
    byes: int


class EventPublic(BaseModel):
    code: str
    set_code: str
    set_name: str
    booster_type: str
    host_player_id: str
    status: str
    players: list[PlayerPublic]
    current_round: int = 0
    max_rounds: int = 0
    rounds: list[RoundPublic] = Field(default_factory=list)
    standings: list[StandingPublic] = Field(default_factory=list)


class SessionResponse(BaseModel):
    event: EventPublic
    player_id: str
    token: str


class SetPreview(BaseModel):
    code: str
    name: str
    booster_types: list[str]
    recommended_booster_type: str
    provider: str
    extra: dict[str, Any] = Field(default_factory=dict)
