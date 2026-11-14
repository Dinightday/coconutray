"""Public catalog cache acceptance: expiry, isolation and safe federation reuse."""
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from coconutray.models import Product
from coconutray.network_cache import CatalogCache
from coconutray.network_data import NetworkRepository
from coconutray.network_models import Store
from coconutray.network_search import federated_search
from coconutray.query import interpret_query

STAMP = "2026-11-14T12:00:00+00:00"
STORE = Store("real-a", "Loja A", "https://a.example", mode="real", consent_at=STAMP, endpoint_checked_at=STAMP)
PRODUCT = Product("12", "Mochila preta", Decimal("149.90"), True, stock=5,
                  variant_id="42", variant_label="Preta", description="Catálogo público",
                  source="tray", url="https://a.example/mochila?variante=42%2B1",
                  image_url="https://a.example/mochila.jpg", attributes={"Cor": "Preto"})


@pytest.fixture
def repository(tmp_path):
    return NetworkRepository(tmp_path / "network.sqlite3")


def test_cache_roundtrip_preserves_decimal_urls_properties_and_original_time(repository):
    cache = CatalogCache(repository)
    cache.put(STORE, "mochila", [PRODUCT], STAMP)
    products, fetched_at = cache.get(STORE, "mochila")
    assert products == [PRODUCT]
    assert products[0].price == Decimal("149.90")
    assert products[0].price.as_tuple().exponent == -2
    assert products[0].url == "https://a.example/mochila?variante=42%2B1"
    assert fetched_at == STAMP


def test_entry_expires_at_sixty_seconds_without_extending_on_read(repository):
    clock = [1000.0]
    cache = CatalogCache(repository, clock=lambda: clock[0])
    cache.put(STORE, "mochila", [PRODUCT], STAMP)
    clock[0] = 1059.999
    assert cache.get(STORE, "mochila") is not None
    clock[0] = 1060.0
    assert cache.get(STORE, "mochila") is None


def test_namespace_isolates_participant_domain_query_and_demo_mode(repository):
    cache = CatalogCache(repository)
    cache.put(STORE, "private-query-123", [PRODUCT], STAMP)
    assert cache.get(replace(STORE, id="another"), "private-query-123") is None
    assert cache.get(replace(STORE, domain="https://b.example"), "private-query-123") is None
    assert cache.get(STORE, "other-query") is None
    assert cache.get(replace(STORE, mode="demo"), "private-query-123") is None
    with repository.connection() as db:
        row = db.execute("SELECT cache_key,mode,payload FROM network_cache").fetchone()
    assert len(row["cache_key"]) == 64
    assert "private-query-123" not in row["cache_key"]
    assert "private-query-123" not in row["payload"]
    assert row["mode"] == "real"
    assert set(json.loads(row["payload"])) == {"version", "fetched_at", "products"}


@pytest.mark.parametrize("corruption", ["json", "price", "source", "url", "timestamp", "mode"])
def test_corrupt_cache_is_a_miss_instead_of_a_search_failure(repository, corruption):
    cache = CatalogCache(repository)
    cache.put(STORE, "mochila", [PRODUCT], STAMP)
    with repository.connection() as db:
        row = db.execute("SELECT payload FROM network_cache").fetchone()
        payload = json.loads(row["payload"])
        if corruption == "price":
            payload["products"][0]["price"] = "NaN"
        elif corruption == "source":
            payload["products"][0]["source"] = "demo"
        elif corruption == "url":
            payload["products"][0]["url"] = "https://evil.example/checkout"
        elif corruption == "timestamp":
            payload["fetched_at"] = "not-a-date"
        serialized = "not-json" if corruption == "json" else json.dumps(payload)
        db.execute("UPDATE network_cache SET payload=?,mode=?", (serialized, "demo" if corruption == "mode" else "real"))
    assert cache.get(STORE, "mochila") is None


def test_unknown_availability_and_missing_url_are_preserved_without_invention(repository):
    product = replace(PRODUCT, available=False, stock=None, availability_known=False, url=None, image_url=None)
    cache = CatalogCache(repository)
    cache.put(STORE, "mochila", [product], STAMP)
    cached, _ = cache.get(STORE, "mochila")
    assert cached[0].available is False
    assert cached[0].availability_known is False
    assert cached[0].stock is None
    assert cached[0].url is None
    assert cached[0].image_url is None


@pytest.mark.parametrize("product", [replace(PRODUCT, source="demo"), replace(PRODUCT, url="https://evil.example/cart")])
def test_cache_never_stores_demo_source_or_unapproved_link(repository, product):
    cache = CatalogCache(repository)
    cache.put(STORE, "mochila", [product], STAMP)
    with repository.connection() as db:
        assert db.execute("SELECT count(*) FROM network_cache").fetchone()[0] == 0


class Catalog:
    def __init__(self, products):
        self.products = products
        self.closed = False

    def search(self, query):
        assert query == "mochila"
        return self.products

    def close(self):
        self.closed = True


def test_federation_hit_avoids_client_creation_and_rechecks_current_hard_filters(repository):
    clients = []

    def factory(store):
        client = Catalog([PRODUCT])
        clients.append(client)
        return client

    original = interpret_query("Mochila preta até R$ 180")
    fresh = federated_search([STORE], original, catalog_factory=factory, repository=repository)
    assert len(fresh.matches) == 1 and not fresh.matches[0].cached
    tighter = federated_search([STORE], interpret_query("Mochila preta até R$ 100"), catalog_factory=factory, repository=repository)
    assert tighter.responded == 1 and tighter.matches == []
    hit = federated_search([STORE], original, catalog_factory=factory, repository=repository)
    assert len(hit.matches) == 1 and hit.matches[0].cached
    assert hit.matches[0].fetched_at == fresh.matches[0].fetched_at
    assert hit.matches[0].product == fresh.matches[0].product
    assert len(clients) == 1 and clients[0].closed


def test_corrupt_entry_falls_back_to_one_live_lookup(repository):
    cache = CatalogCache(repository)
    cache.put(STORE, "mochila", [PRODUCT], STAMP)
    with repository.connection() as db:
        db.execute("UPDATE network_cache SET payload='broken'")
    calls = []

    def factory(store):
        calls.append(store.id)
        return Catalog([PRODUCT])

    report = federated_search([STORE], interpret_query("Mochila"), catalog_factory=factory, repository=repository)
    assert report.responded == 1 and not report.failures
    assert len(report.matches) == 1 and not report.matches[0].cached
    assert calls == [STORE.id]


def test_no_repository_keeps_one_live_lookup_per_store_per_search():
    clients = []

    def factory(store):
        client = Catalog([PRODUCT])
        clients.append(client)
        return client

    for _ in range(2):
        report = federated_search([STORE], interpret_query("Mochila"), catalog_factory=factory)
        assert len(report.matches) == 1 and not report.matches[0].cached
    assert len(clients) == 2 and all(c.closed for c in clients)


def test_demo_catalog_is_never_cached_even_with_repository(repository):
    clients = []

    def factory(store):
        client = Catalog([replace(PRODUCT, source="demo", url=None, image_url=None)])
        clients.append(client)
        return client

    demo = replace(STORE, mode="demo")
    for _ in range(2):
        report = federated_search([demo], interpret_query("Mochila"), catalog_factory=factory, repository=repository)
        assert len(report.matches) == 1 and not report.matches[0].cached
    with repository.connection() as db:
        assert db.execute("SELECT count(*) FROM network_cache").fetchone()[0] == 0
    assert len(clients) == 2
