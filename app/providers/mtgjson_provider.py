from __future__ import annotations

import random
import threading
from typing import Any

from app.models import CardData, SetPreview
from app.providers.base import CardProvider


class MtgjsonProvider(CardProvider):
    """Thin adapter around the official mtgjson-sdk.

    The SDK owns booster collation. We deliberately do not reimplement sheet
    weights here. The adapter normalizes SDK card models for the web client.
    """

    name = "mtgjson-sdk"

    def __init__(self) -> None:
        self._sdk = None
        self._lock = threading.RLock()
        self._promo_cache: dict[str, list[Any]] = {}

    def _get_sdk(self):
        with self._lock:
            if self._sdk is None:
                try:
                    from mtgjson_sdk import MtgjsonSDK
                except ImportError as exc:
                    raise RuntimeError(
                        "mtgjson-sdk is not installed. Run scripts/setup.ps1 or pip install -r requirements.txt."
                    ) from exc
                self._sdk = MtgjsonSDK()
            return self._sdk

    @staticmethod
    def _dump(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "dict"):
            return value.dict()
        if hasattr(value, "__dict__"):
            return vars(value)
        return value

    def _normalize(self, raw: Any, *, foil: bool | None = None) -> CardData:
        data = self._dump(raw) or {}
        identifiers = self._dump(data.get("identifiers")) or {}
        scryfall_id = identifiers.get("scryfallId") or identifiers.get("scryfall_id")
        image_url = None
        if scryfall_id and len(scryfall_id) > 2:
            image_url = f"https://cards.scryfall.io/normal/front/{scryfall_id[0]}/{scryfall_id[1]}/{scryfall_id}.jpg"

        type_line = data.get("type") or data.get("typeLine") or data.get("type_line") or ""
        if not type_line:
            types = data.get("types") or []
            type_line = " ".join(types)

        card_id = str(data.get("uuid") or data.get("id") or scryfall_id or f"card-{data.get('name','unknown')}")
        return CardData(
            id=card_id,
            name=str(data.get("name") or "Unknown card"),
            rarity=str(data.get("rarity") or "common").lower(),
            mana_cost=data.get("manaCost") or data.get("mana_cost"),
            mana_value=data.get("manaValue") if data.get("manaValue") is not None else data.get("mana_value"),
            colors=list(data.get("colors") or []),
            color_identity=list(data.get("colorIdentity") or data.get("color_identity") or []),
            type_line=type_line,
            oracle_text=data.get("text") or data.get("oracleText") or data.get("oracle_text"),
            set_code=str(data.get("setCode") or data.get("set_code") or "").upper(),
            number=str(data.get("number") or ""),
            foil=bool(foil if foil is not None else data.get("foil", False)),
            scryfall_id=scryfall_id,
            image_url=image_url,
        )

    @staticmethod
    def _recommended(types: list[str]) -> str:
        lowered = {t.lower(): t for t in types}
        for preferred in ("play", "default", "draft"):
            if preferred in lowered:
                return lowered[preferred]
        for t in types:
            if "collector" not in t.lower():
                return t
        return types[0] if types else "default"

    def preview_set(self, set_code: str) -> SetPreview:
        code = set_code.upper().strip()
        sdk = self._get_sdk()
        with self._lock:
            set_obj = sdk.sets.get(code)
            if not set_obj:
                raise ValueError(f"MTGJSON does not know set {code}.")
            set_data = self._dump(set_obj) or {}
            booster_types = list(sdk.booster.available_types(code) or [])
        if not booster_types:
            raise ValueError(f"Set {code} has no booster configuration in MTGJSON.")
        return SetPreview(
            code=code,
            name=str(set_data.get("name") or code),
            booster_types=booster_types,
            recommended_booster_type=self._recommended(booster_types),
            provider=self.name,
            extra={
                "releaseDate": set_data.get("releaseDate") or set_data.get("release_date"),
                "totalSetSize": set_data.get("totalSetSize") or set_data.get("total_set_size"),
            },
        )

    def open_pack(self, set_code: str, booster_type: str, rng: random.Random | None = None) -> list[CardData]:
        sdk = self._get_sdk()
        with self._lock:
            pack = sdk.booster.open_pack(set_code.upper(), booster_type)
        return [self._normalize(card) for card in pack]

    def _promo_candidates(self, set_code: str) -> list[Any]:
        code = set_code.upper()
        if code in self._promo_cache:
            return self._promo_cache[code]
        sdk = self._get_sdk()
        with self._lock:
            cards = list(
                sdk.cards.search(
                    set_code=code,
                    availability="paper",
                    limit=1000,
                )
                or []
            )
        eligible: list[Any] = []
        for raw in cards:
            data = self._dump(raw) or {}
            rarity = str(data.get("rarity") or "").lower()
            availability = [str(x).lower() for x in (data.get("availability") or [])]
            if rarity not in {"rare", "mythic", "mythic rare"}:
                continue
            if availability and "paper" not in availability:
                continue
            # Avoid obvious tokens/oversized/special non-game pieces.
            if data.get("isToken") or data.get("isOversized"):
                continue
            eligible.append(raw)
        self._promo_cache[code] = eligible
        return eligible

    def choose_promo(self, set_code: str, rng: random.Random | None = None) -> CardData:
        rng = rng or random.Random()
        candidates = self._promo_candidates(set_code)
        if not candidates:
            raise ValueError(f"Could not find rare/mythic promo candidates for {set_code}.")
        card = self._normalize(rng.choice(candidates), foil=True)
        return card
