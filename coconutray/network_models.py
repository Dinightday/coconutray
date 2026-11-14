"""Small contracts for the cooperative network, independent of Streamlit."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from coconutray.models import Product


@dataclass(frozen=True)
class Store:
    id: str
    name: str
    domain: str
    mode: str = "demo"
    address: str = ""
    categories: tuple[str, ...] = ()
    latitude: float | None = None
    longitude: float | None = None
    location_source: str = "merchant"
    tray_name: str = ""
    consent_at: str = ""
    endpoint_checked_at: str = ""
    active: bool = True


@dataclass(frozen=True)
class SearchIntent:
    original: str
    terms: tuple[str, ...]
    constraints: dict[str, str] = field(default_factory=dict)
    max_price: Decimal | None = None
    engine: str = "text"
    warnings: tuple[str, ...] = ()
    search_terms: tuple[str, ...] = ()

    @property
    def catalog_query(self) -> str:
        return " ".join(self.search_terms or self.terms)


@dataclass(frozen=True)
class Match:
    store: Store
    product: Product
    status: str
    score: int
    reasons: tuple[str, ...] = ()
    distance_km: float | None = None
    fetched_at: str = ""
    cached: bool = False

    @property
    def key(self) -> str:
        return f"{self.store.id}:{self.product.id}:{self.product.variant_id or '-'}"


@dataclass
class SearchReport:
    intent: SearchIntent
    matches: list[Match]
    consulted: int
    responded: int
    failures: dict[str, str]
    elapsed: float

