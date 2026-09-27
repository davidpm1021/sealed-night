from __future__ import annotations

import os
from app.providers.base import CardProvider
from app.providers.demo import DemoProvider
from app.providers.mtgjson_provider import MtgjsonProvider


def build_provider() -> CardProvider:
    mode = os.environ.get("CARD_PROVIDER", "mtgjson").strip().lower()
    if mode == "demo":
        return DemoProvider()
    return MtgjsonProvider()
