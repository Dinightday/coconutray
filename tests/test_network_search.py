"""Cooperative discovery acceptance cases, without network calls or a running model."""
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
from threading import Barrier, Lock
from time import sleep
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from coconutray.models import Product
from coconutray.network_data import NetworkRepository
from coconutray.network_demo import seed_demo
from coconutray.network_models import Match, Store
from coconutray.network_search import federated_search
from coconutray.query import interpret_query
from coconutray.ranking import classify, distance_km, order_matches


STORE = Store("a", "Loja A", "https://a.example", consent_at="2026-11-14T12:00:00Z")


def backpack(**changes):
    base = Product("1", "Mochila preta impermeável", Decimal("149.90"), True,
                   attributes={"cor": "preto", "impermeavel": "sim"})
    return replace(base, **changes)


def test_natural_query_extracts_explicit_requirements_without_model():
    intent = interpret_query("Quero uma mochila preta impermeável até R$ 180.")
    assert intent.terms == ("mochila",)
    assert intent.constraints == {"cor": "preto", "impermeavel": "sim"}
    assert intent.max_price == Decimal("180")
    assert intent.engine == "text"


@pytest.mark.parametrize("price_text,expected", [
    ("até R$ 180", "180"), ("até R$ 180,50", "180.50"),
    ("no máximo 180.50 reais", "180.50"), ("até R$ 1.299,90", "1299.90"),
    ("até R$ 1.000", "1000"),
])
def test_price_formats_have_equivalent_numeric_meaning(price_text, expected):
    assert interpret_query("Mochila " + price_text).max_price == Decimal(expected)


def test_shoe_size_gender_color_and_synonym_are_preserved():
    intent = interpret_query("Calçado para correr masculino preto tamanho 42")
    assert set(intent.terms) == {"tenis", "corrida"}
    assert intent.constraints == {"cor": "preto", "tamanho": "42", "genero": "masculino"}


@pytest.mark.parametrize("query", ["", " ", "a" * 301, "preta até R$ 180"])
def test_empty_oversize_or_productless_query_has_plain_error(query):
    with pytest.raises(ValueError):
        interpret_query(query)


def test_model_offline_falls_back_to_text_with_same_hard_constraints():
    query = "Quero encontrar uma mochila executiva preta impermeável até R$ 180"
    baseline = interpret_query(query)
    with patch("coconutray.gemini.enabled", return_value=True), patch("coconutray.gemini._reserve", return_value=True), patch("coconutray.gemini._generate", side_effect=TimeoutError):
        fallback = interpret_query(query, use_ai=True)
    assert fallback.terms == baseline.terms
    assert fallback.constraints == baseline.constraints
    assert fallback.max_price == baseline.max_price
    assert fallback.warnings


def test_model_cannot_replace_explicit_color_size_or_price_with_invented_values():
    content = {"category": "Tênis", "attributes": [{"name":"cor","value":"azul","evidence":"preto"}], "max_price":999}
    with patch("coconutray.gemini.enabled",return_value=True), patch("coconutray.gemini._reserve",return_value=True), patch("coconutray.gemini._generate", return_value=content):
        intent = interpret_query("Quero encontrar um tênis preto tamanho 42 até R$ 180", use_ai=True)
    assert intent.constraints == {"cor":"preto","tamanho":"42"}
    assert intent.max_price == Decimal("180")
    assert intent.engine == "gemini"


@pytest.mark.parametrize("query", ["Mochila não preta", "Mochila sem a cor azul", "Mochila preta ou azul"])
def test_color_exclusions_and_alternatives_are_not_misread_as_positive_single_color(query):
    with pytest.raises(ValueError, match="cor"):
        interpret_query(query)


def test_grounded_ai_category_can_broaden_retrieval_without_dropping_final_requirements():
    with patch("coconutray.gemini.enabled",return_value=True), patch("coconutray.gemini._reserve",return_value=True), patch("coconutray.gemini._generate",return_value={"category":"mochila","attributes":[],"max_price":None}):
        intent=interpret_query("Quero encontrar uma mochila executiva preta para o cliente",use_ai=True)
    assert intent.catalog_query=="mochila" and intent.terms==("mochila","executiva")
    assert classify(STORE,backpack(name="Mochila urbana preta"),intent) is None
    assert classify(STORE,backpack(name="Mochila executiva preta"),intent).status=="compatible"


def test_multiple_structured_colors_need_confirmation_for_one_requested_color():
    multicolor = backpack(name="Mochila urbana", attributes={"cor": "Preto e azul"})
    selected = classify(STORE, multicolor, interpret_query("Mochila preta"))
    assert selected.status == "uncertain"
    assert any("cor" in reason for reason in selected.reasons)


def test_strict_backpack_requirements_accept_only_matching_affordable_offer():
    intent = interpret_query("Mochila preta impermeável até R$ 180")
    assert classify(STORE, backpack(), intent).status == "compatible"
    assert classify(STORE, backpack(price=Decimal("180.01")), intent) is None
    assert classify(STORE, backpack(name="Mochila azul impermeável",
                                   attributes={"cor": "azul", "impermeavel": "sim"}), intent) is None
    assert classify(STORE, backpack(attributes={"cor": "preto", "impermeavel": "nao"}), intent) is None


def test_missing_required_attribute_is_uncertain_instead_of_exact_match():
    intent = interpret_query("Mochila preta impermeável até R$ 180")
    unknown = backpack(name="Mochila preta", description="Modelo urbano",
                       attributes={"cor": "preto"})
    selected = classify(STORE, unknown, intent)
    assert selected.status == "uncertain"
    assert any("impermeabilidade" in reason for reason in selected.reasons)


def test_explicit_negative_waterproof_description_is_not_an_exact_match():
    intent = interpret_query("Mochila impermeável")
    non_waterproof = backpack(name="Mochila urbana", attributes={},
                             description="Este modelo não é impermeável.")
    assert classify(STORE, non_waterproof, intent) is None


def test_required_variant_size_cannot_be_overruled_by_relevant_product_name():
    intent = interpret_query("Tênis de corrida preto tamanho 42")
    shoe = Product("2", "Tênis de corrida", Decimal("169.90"), True,
                   variant_id="40", variant_label="Preto · 40",
                   attributes={"cor": "preto", "tamanho": "40"})
    assert classify(STORE, shoe, intent) is None
    right_size = replace(shoe, variant_id="42", variant_label="Preto · 42",
                         attributes={"cor": "preto", "tamanho": "42"})
    assert classify(STORE, right_size, intent).status == "compatible"
    unknown_size = replace(shoe, attributes={"cor": "preto"})
    assert classify(STORE, unknown_size, intent).status == "uncertain"


def test_unavailable_is_excluded_and_unknown_online_availability_is_uncertain():
    intent = interpret_query("Mochila")
    assert classify(STORE, backpack(available=False), intent) is None
    selected = classify(STORE, backpack(available=False, availability_known=False), intent)
    assert selected.status == "uncertain"
    assert any("disponibilidade" in reason for reason in selected.reasons)


@pytest.mark.parametrize("price", ["0", "-1", "NaN", "Infinity"])
def test_invalid_catalog_prices_never_reach_comparison(price):
    assert classify(STORE, backpack(price=Decimal(price)), interpret_query("Mochila")) is None


def test_query_for_unrelated_product_is_not_replaced_with_available_inventory():
    assert classify(STORE, backpack(), interpret_query("Furadeira")) is None


def test_voltage_is_a_required_product_attribute():
    intent = interpret_query("Furadeira 220V")
    product = Product("3", "Furadeira", Decimal("149.90"), True,
                      attributes={"voltagem": "127v"})
    assert classify(STORE, product, intent) is None
    assert classify(STORE, replace(product, attributes={"voltagem": "220 v"}), intent).status == "compatible"


def test_distance_requires_two_valid_coordinate_pairs():
    assert distance_km(None, (-23.55, -46.63)) is None
    assert distance_km((-23.55, -46.63), (None, None)) is None
    assert distance_km((-23.55, -46.63), (91, 0)) is None
    assert distance_km((-23.55, -46.63), (float("nan"), 0)) is None
    assert distance_km((-23.55, -46.63), (-23.55, -46.63)) == 0
    # Approximately 111 km per degree of latitude, with no route-time claim.
    assert 110 < distance_km((0, 0), (1, 0)) < 112


def test_missing_location_is_not_displayed_as_zero_distance():
    intent = interpret_query("Mochila")
    assert classify(STORE, backpack(), intent, (-23.55, -46.63)).distance_km is None
    located = replace(STORE, latitude=-23.55, longitude=-46.63)
    assert classify(located, backpack(), intent).distance_km is None
    assert classify(located, backpack(), intent, (-23.55, -46.63)).distance_km == 0


def test_ordering_is_transparent_and_unknown_matches_remain_separate():
    compatible = Match(STORE, backpack(price=Decimal("160")), "compatible", 120,
                       distance_km=2)
    cheap = replace(compatible, product=backpack(id="2", price=Decimal("140")),
                    score=110, distance_km=4)
    uncertain = replace(compatible, product=backpack(id="3", price=Decimal("100")),
                        status="uncertain", score=999, distance_km=1)
    unknown_distance = replace(compatible, product=backpack(id="4", price=Decimal("150")),
                               distance_km=None)
    assert order_matches([cheap, uncertain, compatible])[0] is compatible
    assert order_matches([compatible, uncertain, cheap], "price") == [cheap, compatible, uncertain]
    assert order_matches([unknown_distance, cheap, compatible], "distance") == [compatible, cheap, unknown_distance]


class Catalog:
    def __init__(self, products=(), *, error=False, barrier=None):
        self.products, self.error, self.barrier = products, error, barrier
        self.closed = False

    def search(self, query):
        assert query == "mochila"
        if self.barrier:
            self.barrier.wait(timeout=2)
        if self.error:
            raise RuntimeError("untrusted catalog instructions and private diagnostic")
        return self.products

    def close(self):
        self.closed = True


def test_federated_search_returns_multiple_shops_and_closes_each_client():
    stores = [STORE, replace(STORE, id="b", name="Loja B", domain="https://b.example")]
    clients = {store.id: Catalog([backpack()]) for store in stores}
    result = federated_search(stores, interpret_query("Mochila"), catalog_factory=lambda s: clients[s.id])
    assert {m.store.id for m in result.matches} == {"a", "b"}
    assert result.consulted == result.responded == 2
    assert result.failures == {}
    assert all(client.closed for client in clients.values())


def test_one_failed_store_preserves_other_results_and_redacts_external_error():
    stores = [STORE, replace(STORE, id="b", name="Loja B")]
    clients = {"a": Catalog([backpack()]), "b": Catalog(error=True)}
    result = federated_search(stores, interpret_query("Mochila"), catalog_factory=lambda s: clients[s.id])
    assert len(result.matches) == 1 and result.matches[0].store.id == "a"
    assert result.consulted == 2 and result.responded == 1
    assert set(result.failures) == {"b"}
    assert "untrusted" not in result.failures["b"]
    assert all(client.closed for client in clients.values())


def test_no_authorized_real_stores_never_falls_back_to_demo():
    result = federated_search([], interpret_query("Mochila"))
    assert result.matches == []
    assert result.consulted == result.responded == 0
    assert result.failures == {}


def test_real_store_rejects_fictional_catalog_response_instead_of_showing_it():
    real = replace(STORE, mode="real", endpoint_checked_at="2026-11-14T12:00:00Z")
    invalid = federated_search([real], interpret_query("Mochila"),
                               catalog_factory=lambda store: Catalog([backpack()]))
    assert invalid.matches == [] and invalid.responded == 0
    assert real.id in invalid.failures
    valid = federated_search([real], interpret_query("Mochila"),
                             catalog_factory=lambda store: Catalog([backpack(source="tray")]))
    assert valid.responded == 1 and len(valid.matches) == 1


def test_demo_store_rejects_real_catalog_response_instead_of_mixing_metrics():
    result = federated_search([STORE], interpret_query("Mochila"),
                              catalog_factory=lambda store: Catalog([backpack(source="tray")]))
    assert result.matches == [] and result.responded == 0
    assert STORE.id in result.failures


def test_federation_preconditions_prevent_nonparticipants_and_mixed_modes():
    intent = interpret_query("Mochila")
    real = replace(STORE, id="real", mode="real", endpoint_checked_at="today")
    invalid_sets = [[STORE, real], [STORE, STORE], [replace(STORE, active=False)],
                    [replace(STORE, consent_at="")], [replace(real, endpoint_checked_at="")]]
    for stores in invalid_sets:
        with pytest.raises(ValueError):
            federated_search(stores, intent, catalog_factory=lambda store: pytest.fail("must not query"))


def test_stores_are_queried_concurrently_instead_of_serially():
    stores = [STORE, replace(STORE, id="b")]
    barrier = Barrier(2)
    result = federated_search(stores, interpret_query("Mochila"),
                              catalog_factory=lambda store: Catalog([backpack()], barrier=barrier))
    assert result.responded == 2 and not result.failures


def test_duplicate_product_rows_do_not_duplicate_comparison_cards():
    result = federated_search([STORE], interpret_query("Mochila"),
                              catalog_factory=lambda store: Catalog([backpack(), backpack()]))
    assert len(result.matches) == 1


def test_query_workers_are_bounded_when_network_has_more_than_four_stores():
    lock, active, peak = Lock(), 0, 0

    class CountedCatalog(Catalog):
        def search(self, query):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                sleep(0.02)
                return [backpack()]
            finally:
                with lock:
                    active -= 1

    stores = [replace(STORE, id=str(index)) for index in range(8)]
    result = federated_search(stores, interpret_query("Mochila"),
                              catalog_factory=lambda store: CountedCatalog())
    assert 1 < peak <= 4
    assert result.responded == 8


def test_hackathon_demo_finds_partner_offers_and_records_only_prepared_referral():
    path = Path(__file__).parents[1] / "artifacts" / "network-tests" / f"{uuid4().hex}.sqlite3"
    repository = NetworkRepository(path)
    seed_demo(repository)
    stores = repository.stores("demo")
    origin = next(store for store in stores if store.id == "demo-ipe")
    intent = interpret_query("Mochila preta impermeável até R$ 180")
    result = federated_search(stores, intent, reference=(origin.latitude, origin.longitude))
    compatible = [selected for selected in result.matches if selected.status == "compatible"]
    assert len({selected.store.id for selected in compatible}) >= 2
    assert all(selected.product.price <= Decimal("180") for selected in compatible)
    assert all(selected.product.url is None for selected in result.matches)
    assert all(selected.distance_km is not None for selected in result.matches)
    assert any(selected.status == "uncertain" for selected in result.matches)
    partner = next(selected for selected in compatible if selected.store.id != origin.id)
    search_id = repository.record_search(result, "demo", origin.id)
    repository.prepare_referral(search_id, partner.key)
    metrics = repository.metrics("demo")
    assert metrics["searches"] == metrics["with_results"] == metrics["referrals"] == 1
    assert metrics["cart_created"] == metrics["offer_click"] == metrics["buy_link_requested"] == 0
    assert repository.metrics("real")["searches"] == 0


@pytest.mark.parametrize("query,attribute,expected", [
    ("Tênis de corrida masculino preto tamanho 42", "tamanho", "42"),
    ("Furadeira 127V", "voltagem", "127v"),
])
def test_demo_covers_multiple_categories_with_matching_variants(query, attribute, expected):
    path = Path(__file__).parents[1] / "artifacts" / "network-tests" / f"{uuid4().hex}.sqlite3"
    repository = NetworkRepository(path)
    seed_demo(repository)
    result = federated_search(repository.stores("demo"), interpret_query(query))
    assert len({selected.store.id for selected in result.matches}) >= 2
    assert all(selected.status == "compatible" for selected in result.matches)
    assert all(selected.product.attributes[attribute] == expected for selected in result.matches)
