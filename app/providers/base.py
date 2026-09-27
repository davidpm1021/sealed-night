from __future__ import annotations

from abc import ABC, abstractmethod
import random
from app.models import CardData, SetPreview


class CardProvider(ABC):
    name = "base"

    @abstractmethod
    def preview_set(self, set_code: str) -> SetPreview:
        raise NotImplementedError

    @abstractmethod
    def open_pack(self, set_code: str, booster_type: str, rng: random.Random | None = None) -> list[CardData]:
        raise NotImplementedError

    @abstractmethod
    def choose_promo(self, set_code: str, rng: random.Random | None = None) -> CardData:
        raise NotImplementedError
