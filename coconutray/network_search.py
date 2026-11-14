"""Controlled federation: one isolated client per store, no silent demo fallback."""
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import replace
from time import monotonic

from coconutray.network_cache import CatalogCache
from coconutray.network_data import now
from coconutray.network_demo import DemoNetworkCatalog
from coconutray.network_models import SearchIntent, SearchReport, Store
from coconutray.ranking import classify, order_matches
from coconutray.tray import TrayClient


def federated_search(stores: list[Store], intent: SearchIntent, reference=None,
                     catalog_factory=None, timeout: float = 12, *, repository=None) -> SearchReport:
    if timeout <= 0:
        raise ValueError("O tempo de consulta deve ser positivo.")
    if len({s.mode for s in stores}) > 1:
        raise ValueError("Uma pesquisa não pode misturar lojas reais e demonstrativas.")
    if any(not s.active or not s.consent_at or (s.mode == "real" and not s.endpoint_checked_at) for s in stores):
        raise ValueError("Consulte somente lojas participantes com conexão validada.")
    if len({s.id for s in stores}) != len(stores):
        raise ValueError("A lista de lojas contém participantes duplicados.")
    started = monotonic()
    if not stores:
        return SearchReport(intent, [], 0, 0, {}, 0)
    cache = CatalogCache(repository) if repository is not None else None

    def query_store(store):
        cached = cache.get(store, intent.catalog_query) if cache else None
        if cached is not None:
            products, fetched_at = cached
            return [replace(match, fetched_at=fetched_at, cached=True) for product in products
                    if (match := classify(store, product, intent, reference)) is not None]
        if catalog_factory:
            client = catalog_factory(store)
        elif store.mode == "demo":
            client = DemoNetworkCatalog(store)
        else:
            client = TrayClient(store.domain, [store.domain], timeout=timeout, total_timeout=timeout)
        try:
            # Real participants were checked with store-info during registration.
            # Search's total budget includes the MCP handshake and variant calls.
            products = client.search(intent.catalog_query)
            expected_source = "demo" if store.mode == "demo" else "tray"
            if any(product.source != expected_source for product in products):
                raise ValueError("O catálogo retornou dados de outro ambiente.")
            fetched_at = now()
            if cache:
                cache.put(store, intent.catalog_query, products, fetched_at)
            return [replace(match, fetched_at=fetched_at) for product in products
                    if (match := classify(store, product, intent, reference)) is not None]
        finally:
            client.close()

    matches, failures, responded = [], {}, 0
    pool = ThreadPoolExecutor(max_workers=min(4, len(stores)), thread_name_prefix="coconutray")
    futures = {pool.submit(query_store, store): store for store in stores}
    # Includes all waves when there are more participants than workers.
    deadline = timeout * ((len(stores) + 3) // 4) + 1
    done, pending = wait(futures, timeout=deadline)
    for future in done:
        store = futures[future]
        try:
            matches.extend(future.result())
            responded += 1
        except Exception:
            # No catalog content, credentials or untrusted server error is shown/stored.
            failures[store.id] = "Não foi possível consultar esta loja agora."
    for future in pending:
        future.cancel()
        failures[futures[future].id] = "Esta loja demorou para responder. Tente novamente depois."
    pool.shutdown(wait=False, cancel_futures=True)
    unique = {m.key: m for m in matches}
    return SearchReport(intent, order_matches(list(unique.values())), len(stores), responded,
                        failures, monotonic() - started)
