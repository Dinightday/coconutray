"""Short-lived public Tray catalog cache; no shopper sessions or query text.

Entries contain only normalized public products and their original fetch time.
Every hit is reclassified against the current request by the search layer.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import sqlite3
from time import time

from coconutray.models import Product
from coconutray.network_models import Store
from coconutray.tray import IntegrationError, validate_offer_url, validate_image_url


class CatalogCache:
    def __init__(self, repository, *, ttl: float = 60, clock=time):
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("O prazo do cache deve ser positivo.")
        self.repository = repository
        self.ttl = ttl
        self.clock = clock

    @staticmethod
    def key(store: Store, query: str) -> str:
        # A domain change cannot reuse the previous participant's catalog.
        material = json.dumps([1, store.id, store.domain, query], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _product(row: dict, store: Store) -> Product:
        if not isinstance(row, dict) or row.get("source") != "tray":
            raise ValueError("Invalid public catalog source")
        if not isinstance(row.get("id"), str) or not row["id"] or not isinstance(row.get("name"), str) or not row["name"]:
            raise ValueError("Invalid product identity")
        if not isinstance(row.get("price"), str):
            raise ValueError("Invalid product price")
        price = Decimal(row["price"])
        if not price.is_finite() or price <= 0 or price.as_tuple().exponent < -2:
            raise ValueError("Invalid product price")
        if type(row.get("available")) is not bool or type(row.get("availability_known")) is not bool:
            raise ValueError("Invalid availability")
        stock = row.get("stock")
        if stock is not None and (type(stock) is not int or stock < 0):
            raise ValueError("Invalid stock")
        for key in ("variant_id", "variant_label", "url", "image_url"):
            if row.get(key) is not None and not isinstance(row[key], str):
                raise ValueError("Invalid product field")
        if not isinstance(row.get("description"), str):
            raise ValueError("Invalid product description")
        attributes = row.get("attributes")
        if not isinstance(attributes, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in attributes.items()):
            raise ValueError("Invalid product properties")
        for key in ("url", "image_url"):
            if row.get(key) is not None:
                (validate_image_url if key == "image_url" else validate_offer_url)(row[key], store.domain, resolve=False)
        return Product(row["id"], row["name"], price, row["available"], stock,
                       row.get("variant_id"), row.get("variant_label"), row["description"],
                       "tray", row.get("url"), row.get("image_url"), attributes,
                       row["availability_known"])

    @staticmethod
    def _timestamp(value: str) -> str:
        if not isinstance(value, str) or not datetime.fromisoformat(value).tzinfo:
            raise ValueError("Invalid original fetch time")
        return value

    def get(self, store: Store, query: str) -> tuple[list[Product], str] | None:
        if store.mode != "real":
            return None
        try:
            with self.repository.connection() as db:
                entry = db.execute("SELECT mode,payload,expires_at FROM network_cache WHERE cache_key=?",
                                   (self.key(store, query),)).fetchone()
            if not entry or entry["mode"] != "real" or not math.isfinite(entry["expires_at"]) or entry["expires_at"] <= self.clock():
                return None
            if not isinstance(entry["payload"], str) or len(entry["payload"]) > 5 * 1024 * 1024:
                return None
            payload = json.loads(entry["payload"])
            if not isinstance(payload, dict) or payload.get("version") != 1:
                return None
            rows = payload.get("products")
            if not isinstance(rows, list) or len(rows) > 1000:
                return None
            fetched_at = self._timestamp(payload.get("fetched_at"))
            return [self._product(row, store) for row in rows], fetched_at
        except (sqlite3.Error, ValueError, TypeError, KeyError, InvalidOperation, IntegrationError, OverflowError):
            # Cache corruption or a transient SQLite issue must not stop a search.
            return None

    def put(self, store: Store, query: str, products: list[Product], fetched_at: str) -> None:
        if store.mode != "real":
            return
        try:
            if len(products) > 1000:
                return
            rows = [{**asdict(product), "price": str(product.price)} for product in products]
            # The same validation covers fresh and cached data, including URLs.
            for row in rows:
                self._product(row, store)
            payload = json.dumps({"version": 1, "fetched_at": self._timestamp(fetched_at), "products": rows},
                                 ensure_ascii=False, separators=(",", ":"))
            if len(payload) > 5 * 1024 * 1024:
                return
            expires = self.clock() + self.ttl
            with self.repository.connection() as db:
                db.execute("DELETE FROM network_cache WHERE expires_at<=?", (self.clock(),))
                db.execute("INSERT OR REPLACE INTO network_cache(cache_key,mode,payload,expires_at) VALUES (?,?,?,?)",
                           (self.key(store, query), "real", payload, expires))
        except (sqlite3.Error, ValueError, TypeError, KeyError, InvalidOperation, IntegrationError, OverflowError):
            return
