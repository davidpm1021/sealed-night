from __future__ import annotations

from collections import Counter
from pathlib import Path

from app.models import CardInstance, PlayerRecord


BASIC_PRINTINGS: dict[str, tuple[str, str]] = {
    "Plains": ("BFZ", "250a"),
    "Island": ("BFZ", "255a"),
    "Swamp": ("BFZ", "260a"),
    "Mountain": ("BFZ", "265a"),
    "Forest": ("BFZ", "270a"),
}


def _pool_by_copy_id(player: PlayerRecord) -> dict[str, CardInstance]:
    if player.kit is None:
        raise ValueError("Player has no prerelease kit.")
    cards = {player.kit.promo.copy_id: player.kit.promo}
    for pack in player.kit.packs:
        for card in pack.cards:
            cards[card.copy_id] = card
    return cards


def export_xmage_deck(player: PlayerRecord, *, name: str | None = None) -> str:
    """Render the player's saved sealed deck in XMage .dck format."""
    if player.kit is None:
        raise ValueError("Player has no prerelease kit.")
    if not player.deck.legal_for_sealed:
        raise ValueError("Player deck must contain at least 40 cards.")

    pool = _pool_by_copy_id(player)
    selected: list[CardInstance] = []
    for copy_id in player.deck.main_copy_ids:
        card = pool.get(copy_id)
        if card is None:
            raise ValueError(f"Deck references an unknown sealed-pool card: {copy_id}")
        if not card.set_code or not card.number:
            raise ValueError(f"{card.name} is missing set/collector metadata required by XMage.")
        selected.append(card)

    selected_ids = set(player.deck.main_copy_ids)
    sideboard_cards = [
        card
        for copy_id, card in pool.items()
        if copy_id not in selected_ids
    ]
    counts: Counter[tuple[str, str, str]] = Counter(
        (card.set_code.upper(), card.number, card.name) for card in selected
    )
    sideboard_counts: Counter[tuple[str, str, str]] = Counter(
        (card.set_code.upper(), card.number, card.name) for card in sideboard_cards
        if card.set_code and card.number
    )

    lines: list[str] = []
    if name:
        safe_name = name.replace("\n", " ").replace("\r", " ").strip()
        if safe_name:
            lines.append(f"NAME:{safe_name}")

    for (set_code, number, card_name), count in sorted(
        counts.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2].casefold()),
    ):
        lines.append(f"{count} [{set_code}:{number}] {card_name}")

    for basic in ["Plains", "Island", "Swamp", "Mountain", "Forest"]:
        count = max(0, int(player.deck.basics.get(basic, 0)))
        if count:
            set_code, number = BASIC_PRINTINGS[basic]
            lines.append(f"{count} [{set_code}:{number}] {basic}")

    # In Limited, every opened card is available between games. XMage's DCK
    # format marks sideboard cards with "SB:". We also provide a generous
    # reserve of each basic land so the player can completely rebuild mana
    # between games, matching prerelease's unlimited-basic-land rule.
    for (set_code, number, card_name), count in sorted(
        sideboard_counts.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2].casefold()),
    ):
        lines.append(f"SB: {count} [{set_code}:{number}] {card_name}")

    for basic in ["Plains", "Island", "Swamp", "Mountain", "Forest"]:
        set_code, number = BASIC_PRINTINGS[basic]
        lines.append(f"SB: 40 [{set_code}:{number}] {basic}")

    return "\n".join(lines) + "\n"


def write_xmage_deck(player: PlayerRecord, path: str | Path, *, name: str | None = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(export_xmage_deck(player, name=name), encoding="utf-8")
    return target
