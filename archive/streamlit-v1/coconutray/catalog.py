"""Fictional store: the demo never contacts Tray and never invents checkout URLs."""
import re
import unicodedata
from decimal import Decimal
from uuid import uuid4

from coconutray.models import CartResult, Product, ReviewLine
from coconutray.validation import validate_review


def normalize(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value.lower()) if not unicodedata.combining(c))


def _stem(word: str) -> str:
    if word.endswith("eis"):
        return word[:-3] + "el"
    if word.endswith("uis"):
        return word[:-3] + "ul"
    return word[:-1] if word.endswith("s") else word


DEMO_PRODUCTS = (
    Product("CAD-001", "Caderno universitário azul · 10 matérias", Decimal("24.90"), True, 32,
            variant_id="CAD-001-AZ", variant_label="Azul", description="200 folhas, capa dura."),
    Product("CAD-001", "Caderno universitário verde · 10 matérias", Decimal("24.90"), True, 18,
            variant_id="CAD-001-VD", variant_label="Verde", description="200 folhas, capa dura."),
    Product("CAD-002", "Caderno universitário preto · 1 matéria", Decimal("14.90"), True, 24,
            variant_id="CAD-002-PT", variant_label="Preto", description="80 folhas, capa dura."),
    Product("CAN-001", "Caneta esferográfica azul", Decimal("3.50"), True, 120,
            variant_id="CAN-001-AZ", variant_label="Azul", description="Ponta 1.0 mm."),
    Product("CAN-001", "Caneta esferográfica preta", Decimal("3.50"), True, 90,
            variant_id="CAN-001-PT", variant_label="Preta", description="Ponta 1.0 mm."),
    Product("CAN-001", "Caneta esferográfica vermelha", Decimal("3.50"), False, 0,
            variant_id="CAN-001-VM", variant_label="Vermelha", description="Ponta 1.0 mm. Sem estoque."),
    Product("LAP-001", "Lápis grafite HB", Decimal("2.00"), True, 150, description="Madeira, grafite HB."),
    Product("BOR-001", "Borracha branca", Decimal("1.50"), True, 64, description="Borracha escolar macia."),
    Product("MOC-001", "Mochila escolar azul", Decimal("89.90"), True, 6,
            variant_id="MOC-001-AZ", variant_label="Azul", description="Compartimento principal e bolso frontal."),
    Product("MOC-001", "Mochila escolar rosa", Decimal("89.90"), False, 0,
            variant_id="MOC-001-RS", variant_label="Rosa", description="Produto indisponível."),
)


class DemoCatalog:
    mode = "demo"

    def store_info(self) -> dict:
        return {"store_name": "Papelaria Coconut", "store_id": "demo", "mode": "demo"}

    def products(self) -> list[Product]:
        return list(DEMO_PRODUCTS)

    def search(self, query: str) -> list[Product]:
        stopwords = {"de", "da", "do", "a", "o", "e", "um", "uma", "quero", "materia", "materias"}
        terms = [_stem(t) for t in re.findall(r"[a-z0-9]+", normalize(query)) if t not in stopwords and not t.isdigit()]
        if not terms:
            return []
        # All requested descriptive words must match. No arbitrary substitute products.
        return [p for p in DEMO_PRODUCTS if all(t in {_stem(w) for w in re.findall(r"[a-z0-9]+", normalize(p.name))} for t in terms)]

    def create_cart(self, lines: list[ReviewLine], *, approved: bool) -> CartResult:
        errors = validate_review(lines, approved)
        verified = {(p.id, p.variant_id): p for p in DEMO_PRODUCTS}
        for line in lines:
            if verified.get((line.product.id, line.product.variant_id)) != line.product:
                errors.append("Produto ou preço não corresponde ao catálogo demonstrativo.")
        if errors:
            raise ValueError(" ".join(errors))
        return CartResult(id=str(uuid4()), url=None, mode="demo", items=list(lines),
                          message="Carrinho demonstrativo. Nenhuma integração ou compra real foi realizada.")
