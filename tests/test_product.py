from types import SimpleNamespace
import pytest
from app.providers.mtgjson_provider import MtgjsonProvider


def card(name='Rare', **extra):
    return dict(uuid=name, name=name, setCode='TST', rarity='rare', availability=['paper'],
                finishes=['foil', 'nonfoil'], **extra)


def provider(cards):
    p = MtgjsonProvider()
    calls = []
    def search(**kwargs):
        calls.append(kwargs)
        found = [c for c in cards if c['setCode'] == kwargs['set_code']]
        return found[kwargs['offset']:kwargs['offset']+kwargs['limit']]
    p._sdk = SimpleNamespace(cards=SimpleNamespace(search=search))
    return p, calls


def test_marked_promos_preferred_and_back_faces_excluded():
    stamped = card('Stamped', promoTypes=['prerelease'])
    back = card('Back', side='b', promoTypes=['prerelease'])
    p, _ = provider([card(), stamped, back])
    assert p.choose_promo('TST').name == 'Stamped'
    assert 'prerelease-marked' in p.product_note('TST')


def test_associated_promo_set_and_model_aliases():
    from mtgjson_sdk.models.cards import CardSet
    raw = card('Stamped', promoTypes=['prerelease'], type='Creature', layout='normal',
               manaValue=3, number='1', borderColor='black', frameVersion='2015')
    raw['setCode'] = 'PTST'
    model = CardSet.model_validate(raw)
    p, _ = provider([raw])
    assert p._dump(model)['promoTypes'] == ['prerelease']
    assert p.choose_promo('TST').set_code == 'PTST'


def test_fallback_deduplicates_names_and_filters_variants():
    p, _ = provider([card(), card(), card('Variant', isAlternative=True),
                     card('Buy a box', isPromo=True), card('Digital', isOnlineOnly=True)])
    assert len(p._promo_candidates('TST')) == 1
    assert 'simulated foil' in p.product_note('TST')


def test_full_pagination_and_foil_metadata():
    p, calls = provider([card(str(i)) for i in range(1001)])
    assert len(p._promo_candidates('TST')) == 1001
    assert any(c['offset'] == 1000 for c in calls)
    normalized = p._normalize(card(isFoil=True, identifiers='{"scryfallId":"abc123"}'))
    assert normalized.foil and normalized.scryfall_id == 'abc123'


def test_unsupported_products_and_empty_pools_fail_closed():
    with pytest.raises(ValueError):
        MtgjsonProvider._recommended(['collector', 'theme'])
    p, _ = provider([card('Invalid', isOversized=True)])
    with pytest.raises(ValueError):
        p.product_note('TST')


def test_separate_image_identifiers_are_cached_for_saved_cards():
    calls = []
    def lookup(uuid):
        calls.append(uuid)
        return {'scryfallId': '6098d8be-4e3f-455d-8799-91435bf45a1c'}
    p = MtgjsonProvider()
    p._sdk = SimpleNamespace(identifiers=SimpleNamespace(get_identifiers=lookup))
    first = p._normalize(card())
    second = first.model_copy(deep=True)
    p.hydrate_card_images([first, second])
    assert calls == ['Rare']
    assert first.image_url == second.image_url == 'https://cards.scryfall.io/normal/front/6/0/6098d8be-4e3f-455d-8799-91435bf45a1c.jpg'
    assert first.name == second.name == 'Rare'


def test_image_lookup_failure_preserves_card_and_can_retry():
    def unavailable(uuid):
        raise OSError('offline')
    p = MtgjsonProvider()
    p._sdk = SimpleNamespace(identifiers=SimpleNamespace(get_identifiers=unavailable))
    c = p._normalize(card(text='Test rules'))
    before = c.model_dump()
    p.hydrate_card_images([c])
    assert c.model_dump() == before
    p._sdk.identifiers.get_identifiers = lambda uuid: {'scryfallId': 'abc123'}
    p.hydrate_card_images([c])
    assert c.image_url and c.oracle_text == 'Test rules'
