from __future__ import annotations

from abc import ABC, abstractmethod
import random
from app.models import CardData, SetPreview


class CardProvider(ABC):
    name = "base"

    def hydrate_card_images(self, cards: list[CardData]) -> None:
        """Fill display metadata without changing the generated card pool."""
        return None

    def product_note(self, set_code: str) -> str:
        return 'Simulated six-booster kit with a foil promo; physical product contents may differ.'

    @abstractmethod
    def preview_set(self, set_code: str) -> SetPreview:
        raise NotImplementedError

    @abstractmethod
    def open_pack(self, set_code: str, booster_type: str, rng: random.Random | None = None) -> list[CardData]:
        raise NotImplementedError

    @abstractmethod
    def choose_promo(self, set_code: str, rng: random.Random | None = None) -> CardData:
        raise NotImplementedError
