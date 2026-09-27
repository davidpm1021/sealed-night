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
