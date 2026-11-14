"""Business gates and grounded extraction; no network access or model required."""
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from coconutray.catalog import DemoCatalog
from coconutray.extraction import DemoExtractor, OllamaExtractor, validate_analysis
from coconutray.models import ReviewLine
from coconutray.validation import validate_review

SCENARIOS = json.loads((Path(__file__).parents[1] / "data" / "scenarios.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_fictional_scenarios(scenario):
    result = DemoExtractor().analyze(scenario["text"])
    expected = scenario["expected"]
    assert result.engine == "demo-rules"
    assert len(result.items) == expected["items"]
    assert [item.quantity for item in result.items] == expected["quantities"]
    for field in ("customer_name", "fulfillment", "requested_date"):
        if field in expected:
            assert getattr(result, field) == expected[field]
    if expected.get("warning"):
        assert result.warnings
    for item in result.items:
        # The demo must expose the actual source, even for contradictions.
        assert all(fragment.strip() in scenario["text"] for fragment in item.evidence.split(" | "))
    candidates = [DemoCatalog().search(item.query) for item in result.items]
    if "matches" in expected:
        assert [len(products) for products in candidates] == expected["matches"]
    if "available" in expected:
        assert [products[0].available for products in candidates] == expected["available"]


def test_approval_required_and_no_fake_url():
    catalog = DemoCatalog()
    line = ReviewLine(catalog.search("lápis")[0], 2, 0)
    with pytest.raises(ValueError, match="revisão"):
        catalog.create_cart([line], approved=False)
    cart = catalog.create_cart([line], approved=True)
    assert cart.mode == "demo" and cart.url is None
    assert cart.total == Decimal("4.00")
    assert cart.id != catalog.create_cart([line], approved=True).id


@pytest.mark.parametrize("quantity", [0, -1, 1000, True, 1.5, "2", None])
def test_invalid_quantities(quantity):
    line = ReviewLine(DemoCatalog().products()[0], quantity, 0)
    assert validate_review([line], True)


def test_stock_aggregated_for_duplicate_lines():
    product = DemoCatalog().search("mochila azul")[0]
    assert validate_review([ReviewLine(product, 4, 0), ReviewLine(product, 4, 1)], True)


def test_unavailable_product_blocked():
    product = DemoCatalog().search("caneta vermelha")[0]
    with pytest.raises(ValueError, match="indisponível"):
        DemoCatalog().create_cart([ReviewLine(product, 1, 0)], approved=True)


def test_catalog_price_cannot_be_changed():
    catalog = DemoCatalog()
    product = replace(catalog.products()[0], price=Decimal("0.01"))
    with pytest.raises(ValueError, match="catálogo"):
        catalog.create_cart([ReviewLine(product, 1, 0)], approved=True)


@pytest.mark.parametrize("price", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), Decimal("0")])
def test_invalid_financial_values(price):
    product = replace(DemoCatalog().products()[0], price=price)
    assert validate_review([ReviewLine(product, 1, 0)], True)


def test_model_hallucinations_are_removed():
    data = {"items": [{"query": "caneta", "quantity": 99, "attributes": ["dourada"],
                       "evidence": "Quero caneta azul.", "quantity_evidence": "Quero caneta azul."}],
            "customer_name": "Maria", "fulfillment": "retirada", "requested_date": "amanhã",
            "evidence": {"customer_name": "Quero caneta azul.", "fulfillment": "retirar", "requested_date": "amanhã"}}
    result = validate_analysis(data, "Cliente: Quero caneta azul.")
    assert result.items[0].quantity is None
    assert result.items[0].attributes == []
    assert result.customer_name is None and result.fulfillment is None and result.requested_date is None
    assert len(result.warnings) >= 4


def test_model_cannot_use_seller_information_as_customer_request():
    data = {"items": [{"query": "caneta", "quantity": 10, "attributes": [],
                       "evidence": "Temos 10 canetas.", "quantity_evidence": "Temos 10 canetas."}]}
    assert not validate_analysis(data, "Vendedor: Temos 10 canetas.\nCliente: Obrigado.").items


def test_model_grounded_values_are_kept():
    text = "Quero 2 canetas. Sou a Ana. Posso retirar amanhã?"
    data = {"items": [{"query": "canetas", "quantity": 2, "attributes": [],
                       "evidence": "Quero 2 canetas.", "quantity_evidence": "Quero 2 canetas."}],
            "customer_name": "Ana", "fulfillment": "retirada", "requested_date": "amanhã",
            "evidence": {"customer_name": "Sou a Ana.", "fulfillment": "retirar", "requested_date": "amanhã"}}
    result = validate_analysis(data, text)
    assert result.items[0].quantity == 2 and result.customer_name == "Ana"
    assert result.fulfillment == "retirada" and result.requested_date == "amanhã"


@pytest.mark.parametrize("url", ["https://api.example.com", "http://192.168.1.2:11434", "http://localhost@evil.example", "http://localhost/api", "http://localhost?x=1"])
def test_ollama_rejects_external_destinations(url):
    with pytest.raises(ValueError):
        OllamaExtractor(url)


def test_ollama_unavailable_is_comprehensible():
    with patch("httpx.Client.get", side_effect=httpx.ConnectError("offline")):
        status = OllamaExtractor().status()
    assert not status["available"] and not status["model_available"]
    with patch("httpx.Client.post", side_effect=httpx.ConnectError("offline")):
        with pytest.raises(ValueError, match="Ollama"):
            OllamaExtractor().analyze("Cliente: Quero 2 canetas.")


def test_empty_and_oversize_conversation_rejected():
    for text in ("", "   ", "a" * 20001):
        with pytest.raises(ValueError):
            DemoExtractor().analyze(text)


def test_unknown_product_never_substituted():
    assert DemoCatalog().search("livro") == []
    assert DemoCatalog().search("caderno rosa") == []


def test_model_quantity_cannot_leak_from_another_product():
    text = "Quero 10 lápis e caneta azul."
    data = {"items": [{"query": "caneta azul", "quantity": 10, "attributes": [],
                       "evidence": text, "quantity_evidence": text}]}
    assert validate_analysis(data, text).items[0].quantity is None


@pytest.mark.parametrize("quantity", ["-2", "1.5", "2,5"])
def test_demo_does_not_accept_negative_or_fractional_quantity(quantity):
    assert DemoExtractor().analyze(f"Quero {quantity} canetas.").items[0].quantity is None


def test_negated_fulfillment_not_presented_as_preference():
    text = "Quero 2 lápis. Não quero entrega."
    assert DemoExtractor().analyze(text).fulfillment is None
    data = {"items": [], "fulfillment": "entrega", "evidence": {"fulfillment": "entrega"}}
    assert validate_analysis(data, text).fulfillment is None


def test_customer_name_requires_self_identification():
    data = {"items": [], "customer_name": "Maria", "evidence": {"customer_name": "Olá Maria"}}
    assert validate_analysis(data, "Cliente: Olá Maria, quero 2 lápis.").customer_name is None
