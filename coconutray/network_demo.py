"""Four explicitly fictional participants. Never creates an external purchase URL."""
import json
from pathlib import Path
from decimal import Decimal

from coconutray.models import Product
from coconutray.network_models import Store
from coconutray.network_data import NetworkRepository, now

DATA_FILE = Path(__file__).parents[1] / "data" / "network_demo.json"


def demo_data() -> dict:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def seed_demo(repository: NetworkRepository) -> None:
    existing = {s.id for s in repository.stores("demo", active_only=False)}
    for row in demo_data()["stores"]:
        if row["id"] not in existing:
            repository.add_store(Store(**{**row, "categories": tuple(row["categories"])},
                                       mode="demo", location_source="demo", consent_at=now()))


class DemoNetworkCatalog:
    def __init__(self, store: Store):
        if store.mode != "demo":
            raise ValueError("O catálogo demonstrativo exige uma loja fictícia.")
        self.store = store

    def search(self, query: str) -> list[Product]:
        # Filtering and ranking share the same Python rules in both environments.
        return [Product(row["id"], row["name"], Decimal(row["price"]), row["available"],
                        variant_id=row.get("variant_id"), variant_label=row.get("variant_label"),
                        description=row["description"], source="demo", attributes=row["attributes"])
                for row in demo_data()["products"] if row["store"] == self.store.id]

    def close(self) -> None:
        pass
