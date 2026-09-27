from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import random

from app.models import CardData, CardInstance, PackData, PrereleaseKit
from app.providers.base import CardProvider


def _rng_for(event_code: str, player_id: str) -> random.Random:
    digest = hashlib.sha256(f"{event_code}:{player_id}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _instance(card: CardData, *, copy_id: str, source: str) -> CardInstance:
    return CardInstance(**card.model_dump(), copy_id=copy_id, source=source)


def generate_kit(
    provider: CardProvider,
    *,
    event_code: str,
    player_id: str,
    set_code: str,
    booster_type: str,
) -> PrereleaseKit:
    rng = _rng_for(event_code, player_id)
    packs: list[PackData] = []
    for pack_no in range(1, 7):
        cards = provider.open_pack(set_code, booster_type, rng=rng)
        instances = [
            _instance(card, copy_id=f"{player_id}:p{pack_no}:{idx}:{card.id}", source=f"Pack {pack_no}")
            for idx, card in enumerate(cards, start=1)
        ]
        packs.append(PackData(number=pack_no, cards=instances))

    promo_card = provider.choose_promo(set_code, rng=rng)
    promo = _instance(promo_card, copy_id=f"{player_id}:promo:{promo_card.id}", source="Prerelease Promo")
    return PrereleaseKit(
        set_code=set_code,
        booster_type=booster_type,
        promo=promo,
        packs=packs,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
