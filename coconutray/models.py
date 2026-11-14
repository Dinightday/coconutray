"""Shared, deliberately small domain objects. Money never comes from the LLM."""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

Mode = Literal["demo", "real"]


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    price: Decimal
    available: bool
    stock: int | None = None
    variant_id: str | None = None
    variant_label: str | None = None
    description: str = ""
    source: str = "demo"
    url: str | None = None
    image_url: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    availability_known: bool = True


@dataclass
class RequestItem:
    query: str
    quantity: int | None = None
    attributes: list[str] = field(default_factory=list)
    evidence: str = ""
    quantity_evidence: str | None = None


@dataclass
class Analysis:
    items: list[RequestItem] = field(default_factory=list)
    customer_name: str | None = None
    fulfillment: str | None = None
    requested_date: str | None = None
    missing: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    engine: str = "demo"


@dataclass(frozen=True)
class ReviewLine:
    product: Product
    quantity: int
    request_index: int

    @property
    def subtotal(self) -> Decimal:
        return self.product.price * self.quantity


@dataclass
class CartResult:
    id: str
    url: str | None
    mode: Mode
    items: list[ReviewLine]
    message: str = ""

    @property
    def total(self) -> Decimal:
        return sum((line.subtotal for line in self.items), Decimal("0"))
