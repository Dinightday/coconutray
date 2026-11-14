"""Same catalog engine for the demonstration storefront and reusable widget."""
from dataclasses import asdict
from threading import Lock
from time import monotonic

from coconutray.models import ReviewLine
from coconutray.network_demo import DemoNetworkCatalog
from coconutray.network_models import SearchIntent
from coconutray.network_search import federated_search
from coconutray.query import interpret_query
from coconutray.ranking import order_matches
from coconutray.tray import TrayClient, IntegrationError, PartialCartError, validate_offer_url


def store_json(store):
    return {k:v for k,v in asdict(store).items() if k not in {"consent_at"}}


def match_json(match):
    product = asdict(match.product)
    product["price"] = str(match.product.price)
    return {"key":match.key,"store":store_json(match.store),"product":product,
            "status":match.status,"reasons":match.reasons,"distance_km":match.distance_km,
            "fetched_at":match.fetched_at,"cached":match.cached}


class SearchService:
    def __init__(self, repository, catalog_factory=None, client_factory=TrayClient):
        self.repository, self.catalog_factory, self.client_factory = repository, catalog_factory, client_factory
        self.sessions = {}
        self.lock = Lock()

    def store(self, store_id, *, include_paused=False):
        for mode in ("demo", "real"):
            for store in self.repository.stores(mode, active_only=not include_paused):
                if store.id == store_id:
                    return store
        raise ValueError("Esta loja não está disponível na rede.")

    def storefront(self, store_id, query=""):
        store = self.store(store_id)
        intent = interpret_query(query) if query.strip() else SearchIntent("", ())
        report = federated_search([store], intent, catalog_factory=self.catalog_factory, repository=self.repository)
        return {"store":store_json(store), "offers":[match_json(m) for m in report.matches],
                "failed":bool(report.failures), "mode":store.mode}

    def search(self, store_id, query, order="relevance"):
        origin = self.store(store_id)
        intent = interpret_query(query, use_ai=True)
        stores = [s for s in self.repository.stores(origin.mode) if s.id != origin.id]
        reference = (origin.latitude, origin.longitude)
        report = federated_search(stores, intent, reference, catalog_factory=self.catalog_factory, repository=self.repository)
        search_id = self.repository.record_search(report, origin.mode, origin.id)
        with self.lock:
            current = monotonic()
            self.sessions = {k:v for k,v in self.sessions.items() if current-v[0] < 900}
            if hasattr(self,"cart_claims"):
                self.cart_claims = {marker for marker in self.cart_claims if marker[0] in self.sessions}
            if len(self.sessions) >= 1000:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[search_id] = (current, report, origin)
        return {"search_id":search_id,"mode":origin.mode,"origin":store_json(origin),
                "offers":[match_json(m) for m in order_matches(report.matches, order)],
                "constraints":report.intent.constraints,"max_price":str(report.intent.max_price) if report.intent.max_price else None,
                "consulted":report.consulted,"responded":report.responded,"elapsed":report.elapsed,
                "failed_stores":[self.store(k, include_paused=True).name for k in report.failures],
                "warnings":report.intent.warnings,"engine":report.intent.engine}

    def selection(self, search_id, key):
        with self.lock:
            session = self.sessions.get(search_id)
        if not session or monotonic()-session[0] >= 900:
            raise ValueError("Esta pesquisa expirou. Faça uma nova busca para conferir as ofertas.")
        match = next((m for m in session[1].matches if m.key == key), None)
        if match is None:
            raise ValueError("A oferta não pertence a esta pesquisa.")
        # A participant's withdrawal applies even to an older search snapshot.
        self.store(match.store.id)
        self.store(session[2].id)
        return match

    def handoff(self, search_id, key, prepare_cart=False):
        match = self.selection(search_id, key)
        if match.store.mode == "demo":
            self.repository.prepare_referral(search_id,key)
            self.repository.record_event(search_id,key,"buy_link_requested")
            return {"mode":"demo","url":None,"message":"Encaminhamento demonstrativo registrado. Em uma loja real, você seguiria para a oferta oficial. Esta oferta é fictícia e não tem checkout ou pagamento."}
        url = match.product.url
        if prepare_cart:
            if match.status != "compatible" or not match.product.available:
                raise ValueError("Confirme os requisitos e a disponibilidade antes de preparar um carrinho.")
            # A claim is atomic: uncertain writes must never be blindly repeated.
            with self.lock:
                marker = (search_id,key)
                if not hasattr(self,"cart_claims"):
                    self.cart_claims = set()
                if marker in self.cart_claims:
                    raise ValueError("Este carrinho já foi solicitado. Confira a loja antes de tentar novamente.")
                self.cart_claims.add(marker)
            client = self.client_factory(match.store.domain,[match.store.domain],timeout=8)
            try:
                result = client.create_cart([ReviewLine(match.product,1,0)],approved=True)
                url = result.url
                self.repository.record_event(search_id,key,"cart_created")
            except PartialCartError as exc:
                if not exc.cart_url:
                    raise ValueError("A loja pode ter recebido a solicitação. Confira diretamente com ela antes de repetir.") from exc
                url = validate_offer_url(exc.cart_url,match.store.domain)
            finally:
                client.close()
        if not url:
            raise ValueError("O catálogo não forneceu um link oficial desta oferta.")
        url = validate_offer_url(url,match.store.domain)
        self.repository.prepare_referral(search_id,key)
        self.repository.record_event(search_id,key,"buy_link_requested")
        return {"mode":"real","url":url,"message":"Continue na loja escolhida. Encaminhamento solicitado não comprova compra."}
