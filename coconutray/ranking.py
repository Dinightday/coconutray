"""Transparent hard filters precede any ranking or presentation."""
from __future__ import annotations

import math
import re

from coconutray.models import Product
from coconutray.network_data import now, valid_coordinates
from coconutray.network_models import Match, SearchIntent, Store
from coconutray.query import color_value, color_values, normalize, stem


def distance_km(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float | None:
    if not a or not b or not valid_coordinates(*a) or not valid_coordinates(*b):
        return None
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat, dlon = lat2 - lat1, math.radians(b[1] - a[1])
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, value))))


def product_attributes(product: Product) -> dict[str, str]:
    attrs = {normalize(str(k)).strip(): normalize(str(v)).strip() for k, v in product.attributes.items()}
    aliases = {"color": "cor", "colour": "cor", "size": "tamanho", "voltage": "voltagem",
               "voltagem (v)": "voltagem", "waterproof": "impermeavel", "gender": "genero"}
    attrs = {aliases.get(k, k): v for k, v in attrs.items()}
    if "cor" in attrs:
        attrs["cor"] = "desconhecido" if len(color_values(attrs["cor"])) > 1 else (color_value(attrs["cor"]) or attrs["cor"])
    elif color_value(product.variant_label or product.name):
        attrs["cor"] = color_value(product.variant_label or product.name)
    variant = normalize(product.variant_label or "")
    text = normalize(" ".join((product.name, product.description, variant)))
    if "tamanho" not in attrs:
        size = re.search(r"\b(?:tamanho|tam\.?)\s*(\d{1,3})\b", variant)
        if size:
            attrs["tamanho"] = size[1]
    if "voltagem" not in attrs:
        voltage = re.search(r"\b(127|220|110)\s*v\b", variant or text)
        if voltage:
            attrs["voltagem"] = voltage[1] + "v"
    elif attrs["voltagem"]:
        attrs["voltagem"] = attrs["voltagem"].replace(" ", "")
    if "impermeavel" not in attrs and re.search(r"\bimpermeavel\b", text):
        if not re.search(r"impermeavel.{0,20}(?:nao informad|a confirmar)", text):
            attrs["impermeavel"] = "nao" if re.search(r"(?:nao|sem)\s+(?:\w+\s+){0,4}impermeavel", text) else "sim"
    if "impermeavel" in attrs:
        attrs["impermeavel"] = {"true": "sim", "1": "sim", "yes": "sim", "false": "nao", "0": "nao", "no": "nao"}.get(attrs["impermeavel"], attrs["impermeavel"])
    if "genero" not in attrs:
        for value in ("masculino", "feminino", "unissex"):
            if re.search(rf"\b{value}\b", text):
                attrs["genero"] = value
    return attrs


def classify(store: Store, product: Product, intent: SearchIntent,
             reference: tuple[float, float] | None = None) -> Match | None:
    if not product.price.is_finite() or product.price <= 0:
        return None
    if product.availability_known and not product.available:
        return None
    if intent.max_price is not None and product.price > intent.max_price:
        return None
    corpus = " ".join((product.name, product.description, product.variant_label or "", *map(str, product.attributes.values())))
    words = {stem(w) for w in re.findall(r"[a-z0-9]+", normalize(corpus))}
    if not all(term in words for term in intent.terms):
        return None
    attrs = product_attributes(product)
    missing = []
    confirmed = []
    labels = {"cor": "cor", "tamanho": "tamanho", "impermeavel": "impermeabilidade",
              "genero": "público informado", "voltagem": "voltagem"}
    for key, requested in intent.constraints.items():
        actual = attrs.get(key)
        if actual in {None, "", "desconhecido", "nao informado", "nao informada"}:
            missing.append(f"Confirmar {labels.get(key, key)} com a loja")
        elif actual != requested:
            return None
        else:
            confirmed.append(f"{labels.get(key, key).capitalize()}: {requested}")
    if not product.availability_known:
        missing.append("Confirmar disponibilidade on-line")
    if intent.max_price is not None:
        confirmed.append("Dentro do preço máximo")
    title_words = {stem(w) for w in re.findall(r"[a-z0-9]+", normalize(product.name))}
    score = 100 + 10 * sum(t in title_words for t in intent.terms) + 5 * len(confirmed)
    return Match(store, product, "uncertain" if missing else "compatible", score,
                 tuple(missing + confirmed), distance_km(reference, (store.latitude, store.longitude)), now())


def order_matches(matches: list[Match], order: str = "relevance") -> list[Match]:
    # A cheaper uncertain product never appears before verified requirements.
    if order == "price":
        key = lambda m: (m.status != "compatible", m.product.price, -m.score, m.store.name, m.key)
    elif order == "distance":
        key = lambda m: (m.status != "compatible", m.distance_km is None,
                         m.distance_km if m.distance_km is not None else math.inf,
                         -m.score, m.product.price, m.store.name, m.key)
    else:
        key = lambda m: (m.status != "compatible", -m.score, m.product.price, m.store.name, m.key)
    return sorted(matches, key=key)
