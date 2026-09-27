from __future__ import annotations

import random
from app.models import CardData, SetPreview
from app.providers.base import CardProvider


COLORS = [
    ("W", "Sun"),
    ("U", "Tide"),
    ("B", "Grave"),
    ("R", "Ember"),
    ("G", "Grove"),
]


class DemoProvider(CardProvider):
    name = "demo"

    def __init__(self) -> None:
        self.cards = self._build_cards()

    def _build_cards(self) -> list[CardData]:
        cards: list[CardData] = []
        for color, word in COLORS:
            for i in range(1, 13):
                rarity = "common" if i <= 8 else "uncommon" if i <= 10 else "rare" if i == 11 else "mythic"
                card_id = f"demo-{color}-{i}"
                type_line = "Creature — Adventurer" if i % 3 else "Instant"
                cards.append(
                    CardData(
                        id=card_id,
                        name=f"{word} {['Scout','Adept','Keeper','Guide','Warden','Seeker'][i % 6]} {i}",
                        rarity=rarity,
                        mana_cost=f"{{{max(1, i % 5)}}}{{{color}}}",
                        mana_value=float(max(2, (i % 5) + 1)),
                        colors=[color],
                        color_identity=[color],
                        type_line=type_line,
                        oracle_text="Demo card for testing the prerelease flow.",
                        set_code="DEMO",
                        number=str(len(cards) + 1),
                    )
                )
        for i in range(1, 8):
            cards.append(
                CardData(
                    id=f"demo-land-{i}",
                    name=f"Waystone Expanse {i}",
                    rarity="common",
                    mana_value=0,
                    type_line="Land",
                    oracle_text="{T}: Add one mana of any color.",
                    set_code="DEMO",
                    number=str(len(cards) + 1),
                )
            )
        return cards

    def preview_set(self, set_code: str) -> SetPreview:
        return SetPreview(
            code="DEMO",
            name="Demo Prerelease Set",
            booster_types=["play"],
            recommended_booster_type="play",
            provider=self.name,
            extra={"note": "Synthetic cards used to test the app without downloading card data."},
        )

    def _pick(self, rarity: str, rng: random.Random) -> CardData:
        pool = [c for c in self.cards if c.rarity == rarity]
        return rng.choice(pool).model_copy(deep=True)

    def open_pack(self, set_code: str, booster_type: str, rng: random.Random | None = None) -> list[CardData]:
        rng = rng or random.Random()
        pack: list[CardData] = []
        rare_rarity = "mythic" if rng.random() < 0.14 else "rare"
        pack.append(self._pick(rare_rarity, rng))
        pack.extend(self._pick("uncommon", rng) for _ in range(3))
        pack.extend(self._pick("common", rng) for _ in range(9))
        lands = [c for c in self.cards if "Land" in c.type_line]
        pack.append(rng.choice(lands).model_copy(deep=True))
        return pack

    def choose_promo(self, set_code: str, rng: random.Random | None = None) -> CardData:
        rng = rng or random.Random()
        rarity = "mythic" if rng.random() < 0.14 else "rare"
        card = self._pick(rarity, rng)
        card.foil = True
        return card
