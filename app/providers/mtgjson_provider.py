from __future__ import annotations

import json
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
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump(by_alias=True)
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
            foil=bool(foil if foil is not None else data.get("isFoil", data.get("foil", False))),
            scryfall_id=scryfall_id,
            image_url=image_url,
        )

    @staticmethod
    def _recommended(types: list[str]) -> str:
        lowered = {t.lower(): t for t in types}
        for preferred in ("play", "default", "draft"):
            if preferred in lowered:
                return lowered[preferred]
        raise ValueError('No supported Play/Draft/default booster. Collector, theme and seeded products are not modeled.')

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
            booster_types=[t for t in booster_types if t.lower() in {"play", "draft", "default"}],
            recommended_booster_type=self._recommended(booster_types),
            provider=self.name,
            extra={
                "releaseDate": set_data.get("releaseDate") or set_data.get("release_date"),
                "totalSetSize": set_data.get("totalSetSize") or set_data.get("total_set_size"),
                "product_note": self.product_note(code),
            },
        )

    def open_pack(self, set_code: str, booster_type: str, rng: random.Random | None = None) -> list[CardData]:
        sdk = self._get_sdk()
        with self._lock:
            pack = sdk.booster.open_pack(set_code.upper(), booster_type, as_dict=True)
        if not pack:
            raise ValueError('MTGJSON returned an empty booster; no kit was saved.')
        return [self._normalize(card) for card in pack]

    def _set_cards(self, code):
        sdk = self._get_sdk()
        cards = []
        with self._lock:
            for offset in range(0, 100000, 1000):
                page = list(sdk.cards.search(set_code=code, availability='paper', limit=1000, offset=offset) or [])
                cards.extend(page)
                if len(page) < 1000:
                    return cards
        raise ValueError('Set card scan exceeded its safe limit.')

    def _promo_candidates(self, set_code: str) -> list[Any]:
        code = set_code.upper()
        with self._lock:
            if code in self._promo_cache:
                return self._promo_cache[code]
            main = self._set_cards(code)
            # Standard prerelease printings may be stored in the associated P<SET> promo set.
            promo = self._set_cards('P' + code)
            marked, fallback = {}, {}
            for raw in main + promo:
                data = self._dump(raw) or {}
                if str(data.get('rarity', '')).lower() not in {'rare', 'mythic', 'mythic rare'}:
                    continue
                if 'paper' not in (data.get('availability') or []) or 'foil' not in (data.get('finishes') or []):
                    continue
                if any(data.get(k) for k in ['isToken', 'isOversized', 'isOnlineOnly', 'isFunny']):
                    continue
                if data.get('side') not in {None, 'a'}:
                    continue
                if data.get('layout') in {'token', 'double_faced_token', 'art_series', 'emblem', 'vanguard'}:
                    continue
                name = data.get('name')
                if 'prerelease' in (data.get('promoTypes') or []):
                    marked.setdefault(name, raw)
                elif str(data.get('setCode', '')).upper() == code:
                    if data.get('isPromo') or data.get('isAlternative') or data.get('isFullArt'):
                        continue
                    if set(data.get('frameEffects') or []) & {'showcase', 'extendedart'}:
                        continue
                    fallback.setdefault(name, raw)
            self._promo_cache[code] = list((marked or fallback).values())
            return self._promo_cache[code]

    def product_note(self, set_code):
        candidates = self._promo_candidates(set_code)
        if not candidates:
            raise ValueError(f'No safe foil rare/mythic promo candidates for {set_code}.')
        marked = all('prerelease' in (self._dump(c).get('promoTypes') or []) for c in candidates)
        detail = ('Promo selected from MTGJSON prerelease-marked cards; physical odds are not modeled.' if marked else
                  'No marked prerelease pool found; promo is a simulated foil rare/mythic, one entry per card name.')
        return 'Simulated six-booster kit. ' + detail + ' Seeded packs, extra cards and special product contents are not modeled.'

    def choose_promo(self, set_code: str, rng: random.Random | None = None) -> CardData:
        rng = rng or random.Random()
        candidates = self._promo_candidates(set_code)
        if not candidates:
            raise ValueError(f'Could not find safe promo candidates for {set_code}.')
        return self._normalize(rng.choice(candidates), foil=True)
