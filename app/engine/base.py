from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GameLaunch:
    game_id: str
    endpoint: str


class RulesEngineAdapter(ABC):
    """Boundary for the future XMage bridge.

    Event, sealed-pool, deckbuilding and tournament code should never need to
    know whether games are run by XMage, a manual tabletop mode, or another
    engine.
    """

    @abstractmethod
    def create_match(self, player_decks: dict[str, list[str]]) -> GameLaunch:
        raise NotImplementedError
