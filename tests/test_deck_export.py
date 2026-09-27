from app.engine.deck_export import export_xmage_deck
from app.models import CardInstance, DeckState, PackData, PlayerRecord, PrereleaseKit


def test_export_xmage_deck_uses_printings_and_unlimited_basics():
    card = CardInstance(
        id="uuid-1",
        copy_id="copy-1",
        source="Pack 1",
        name="Grizzly Bears",
        rarity="common",
        set_code="10E",
        number="268",
        type_line="Creature — Bear",
    )
    player = PlayerRecord(
        id="p1",
        name="Dave",
        token="secret",
        joined_at="2026-01-01T00:00:00+00:00",
        kit=PrereleaseKit(
            set_code="10E",
            booster_type="play",
            promo=card.model_copy(update={"copy_id": "promo-1"}),
            packs=[PackData(number=1, cards=[card])],
            generated_at="2026-01-01T00:00:00+00:00",
        ),
        deck=DeckState(
            main_copy_ids=["copy-1"],
            basics={"Plains": 39, "Island": 0, "Swamp": 0, "Mountain": 0, "Forest": 0},
        ),
    )
    text = export_xmage_deck(player, name="Dave Round 1")
    assert "NAME:Dave Round 1" in text
    assert "1 [10E:268] Grizzly Bears" in text
    assert "39 [BFZ:250a] Plains" in text
