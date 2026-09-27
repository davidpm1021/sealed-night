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


class PlayerRecord(BaseModel):
    id: str
    name: str
    token: str
    joined_at: str
    kit: PrereleaseKit | None = None
    deck: DeckState = Field(default_factory=DeckState)


class EventRecord(BaseModel):
    code: str
    set_code: str
    set_name: str
    booster_type: str
    host_player_id: str
    status: Literal["lobby", "deckbuilding", "playing", "complete"] = "lobby"
    created_at: str
    players: dict[str, PlayerRecord] = Field(default_factory=dict)


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


class PlayerPublic(BaseModel):
    id: str
    name: str
    has_kit: bool
    deck_count: int


class EventPublic(BaseModel):
    code: str
    set_code: str
    set_name: str
    booster_type: str
    host_player_id: str
    status: str
    players: list[PlayerPublic]


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
