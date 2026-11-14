"""Persistent network events describe observations, never completed sales."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from coconutray.models import Product
from coconutray.network_data import NetworkRepository, valid_coordinates
from coconutray.network_demo import seed_demo
from coconutray.network_models import Match, SearchIntent, SearchReport, Store


@pytest.fixture
def repository():
    # Keep artifacts inside the authorized workspace on restricted Windows hosts.
    directory = Path(__file__).parents[1] / "artifacts" / "network-tests" / uuid4().hex
    return NetworkRepository(directory / "network.sqlite3")


def participant(identity, *, mode="demo", **changes):
    return Store(identity, f"Loja {identity}", f"https://{identity}.example", mode=mode,
                 consent_at="2026-11-14T12:00:00Z",
                 endpoint_checked_at="2026-11-14T12:00:00Z" if mode == "real" else "",
                 **changes)


def match(store):
    product = Product("12", "Mochila preta", Decimal("149.90"), True,
                      variant_id="42", source="tray" if store.mode == "real" else "demo")
    return Match(store, product, "compatible", 100)


def report(*matches, failures=None):
    return SearchReport(SearchIntent("texto privado da pesquisa", ("mochila",)),
                        list(matches), 2, 1 if failures else 2, failures or {}, 0.25)


def test_demo_seed_is_idempotent_and_never_seeds_real_participants(repository):
    seed_demo(repository)
    seed_demo(repository)
    stores = repository.stores("demo")
    assert len(stores) == 4
    assert all(store.consent_at and store.location_source == "demo" for store in stores)
    assert repository.stores("real") == []


def test_stores_persist_with_declared_sources_and_can_pause_participation(repository):
    store = participant("origem", address="Endereço informado pelo comerciante",
                        categories=("Papelaria", "Bolsas"), latitude=-23.55, longitude=-46.63)
    repository.add_store(store)
    reopened = NetworkRepository(repository.path)
    assert reopened.stores("demo") == [store]
    reopened.set_active(store.id, False)
    assert reopened.stores("demo") == []
    assert reopened.stores("demo", active_only=False)[0].active is False
    reopened.set_active(store.id, True)
    assert reopened.stores("demo")[0].active is True


def test_consent_and_real_connection_check_are_required(repository):
    store = participant("origem")
    with pytest.raises(ValueError, match="autorizar"):
        repository.add_store(replace(store, consent_at=""))
    with pytest.raises(ValueError, match="verificação"):
        repository.add_store(replace(store, mode="real"))
    assert repository.stores("demo") == repository.stores("real") == []


@pytest.mark.parametrize("latitude,longitude", [
    (None, 1), (1, None), (91, 0), (-91, 0), (0, 181), (0, -181),
    (float("nan"), 0), (0, float("inf")), (True, 0), ("-23", "-46"),
])
def test_invalid_or_partial_coordinates_are_not_persisted(repository, latitude, longitude):
    assert not valid_coordinates(latitude, longitude)
    with pytest.raises(ValueError, match="latitude"):
        repository.add_store(participant("origem", latitude=latitude, longitude=longitude))
    assert repository.stores("demo") == []


def test_coordinate_bounds_and_unknown_location_are_valid(repository):
    assert valid_coordinates(-90, -180)
    assert valid_coordinates(90, 180)
    repository.add_store(participant("origem"))
    assert repository.stores("demo")[0].latitude is None


def test_same_domain_cannot_register_twice_in_same_environment(repository):
    store = participant("origem")
    repository.add_store(store)
    with pytest.raises(ValueError, match="já está cadastrada"):
        repository.add_store(replace(store, id="outro"))
    assert len(repository.stores("demo")) == 1


def test_search_metrics_are_persistent_and_modes_stay_separate(repository):
    demo, real = participant("demo"), participant("real", mode="real")
    repository.add_store(demo)
    repository.add_store(real)
    demo_match = match(demo)
    demo_id = repository.record_search(report(demo_match), "demo")
    repository.record_search(report(), "demo")
    real_id = repository.record_search(report(match(real), failures={"outra": "Timeout"}), "real")
    repository.record_event(demo_id, demo_match.key, "offer_click")
    repository.record_event(real_id, match(real).key, "buy_link_requested")
    metrics = NetworkRepository(repository.path).metrics("demo")
    assert metrics["searches"] == 2
    assert metrics["with_results"] == metrics["without_results"] == 1
    assert metrics["stores_consulted"] == 4
    assert metrics["offer_click"] == 1
    assert metrics["buy_link_requested"] == metrics["cart_created"] == 0
    assert metrics["referrals"] == metrics["failures"] == 0
    assert "sales" not in metrics and "vendas" not in metrics
    assert repository.metrics("real")["searches"] == 1
    assert repository.metrics("real")["failures"] == 1


def test_search_rejects_cross_environment_results_atomically(repository):
    demo, real = participant("demo"), participant("real", mode="real")
    repository.add_store(demo)
    repository.add_store(real)
    with pytest.raises(ValueError, match="ambiente"):
        repository.record_search(report(match(demo), match(real)), "demo")
    assert repository.metrics("demo")["searches"] == 0


@pytest.mark.parametrize("mode,wrong_source", [("demo", "tray"), ("real", "demo")])
def test_product_source_must_match_persistent_environment(repository, mode, wrong_source):
    store = participant("origem", mode=mode)
    repository.add_store(store)
    selected = match(store)
    selected = replace(selected, product=replace(selected.product, source=wrong_source))
    with pytest.raises(ValueError, match="origem do produto"):
        repository.record_search(report(selected), mode)
    assert repository.metrics(mode)["searches"] == 0


def test_origin_must_be_active_registered_and_from_same_environment(repository):
    demo, real = participant("demo"), participant("real", mode="real")
    repository.add_store(demo)
    repository.add_store(real)
    for origin_id in ("desconhecida", real.id):
        with pytest.raises(ValueError, match="origem"):
            repository.record_search(report(match(demo)), "demo", origin_id)
    repository.set_active(demo.id, False)
    with pytest.raises(ValueError, match="origem"):
        repository.record_search(report(match(demo)), "demo", demo.id)
    assert repository.metrics("demo")["searches"] == 0


def test_referral_has_named_origin_destination_and_is_idempotent(repository):
    origin, destination = participant("origem"), participant("destino")
    repository.add_store(origin)
    repository.add_store(destination)
    selected = match(destination)
    search_id = repository.record_search(report(selected), "demo", origin.id)
    referral = repository.prepare_referral(search_id, selected.key)
    assert repository.prepare_referral(search_id, selected.key) == referral
    assert repository.metrics("demo")["referrals"] == 1
    assert repository.metrics("real")["referrals"] == 0
    assert repository.recent_referrals("demo") == [{
        "created_at": repository.recent_referrals("demo")[0]["created_at"],
        "origin": origin.name, "destination": destination.name,
        "product_name": selected.product.name,
    }]


def test_self_referral_or_consumer_search_cannot_count_as_cooperation(repository):
    store = participant("origem")
    repository.add_store(store)
    selected = match(store)
    for origin_id in (None, store.id):
        search_id = repository.record_search(report(selected), "demo", origin_id)
        with pytest.raises(ValueError, match="outra loja"):
            repository.prepare_referral(search_id, selected.key)
    assert repository.metrics("demo")["referrals"] == 0


def test_events_require_observed_search_offer_and_ignore_rerun_duplicates(repository):
    store = participant("origem")
    repository.add_store(store)
    selected = match(store)
    search_id = repository.record_search(report(selected), "demo")
    for kind in ("offer_click", "buy_link_requested", "cart_created"):
        first = repository.record_event(search_id, selected.key, kind)
        assert repository.record_event(search_id, selected.key, kind) == first
        assert repository.metrics("demo")[kind] == 1
    for invalid_kind in ("sale", "paid_order", "page_loaded"):
        with pytest.raises(ValueError, match="inválido"):
            repository.record_event(search_id, selected.key, invalid_kind)
    with pytest.raises(ValueError, match="não pertence"):
        repository.record_event(search_id, "forged:product", "offer_click")
    with pytest.raises(ValueError, match="não pertence"):
        repository.record_event("unknown", selected.key, "offer_click")


def test_customer_search_text_is_not_persisted(repository):
    store = participant("origem")
    repository.add_store(store)
    repository.record_search(report(match(store)), "demo")
    with sqlite3.connect(repository.path) as db:
        dump = "\n".join(db.iterdump())
    assert "texto privado da pesquisa" not in dump


@pytest.mark.parametrize("mode", ["", "paid", "DEMO", None])
def test_unknown_modes_are_rejected(repository, mode):
    with pytest.raises(ValueError):
        repository.metrics(mode)
    with pytest.raises(ValueError):
        repository.stores(mode)
    with pytest.raises(ValueError):
        repository.record_search(report(), mode)
