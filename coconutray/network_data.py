"""SQLite registry and observable events for Coconutray v1.0.

Separate tables preserve the previous prototype's data without reinterpreting it.
No customer messages, names, cart sessions or personal location are persisted.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from uuid import uuid4

from coconutray.network_models import SearchReport, Store


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def valid_coordinates(latitude, longitude) -> bool:
    return (type(latitude) in (int, float) and type(longitude) in (int, float)
            and math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180)


def check_mode(mode: str) -> str:
    if mode not in {"demo", "real"}:
        raise ValueError("Escolha demonstração ou lojas reais.")
    return mode


class NetworkRepository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS network_stores (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('demo','real')),
                    address TEXT NOT NULL, categories TEXT NOT NULL,
                    latitude REAL, longitude REAL, location_source TEXT NOT NULL,
                    tray_name TEXT NOT NULL, consent_at TEXT NOT NULL,
                    endpoint_checked_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(domain, mode)
                );
                CREATE TABLE IF NOT EXISTS network_searches (
                    id TEXT PRIMARY KEY, mode TEXT NOT NULL, created_at TEXT NOT NULL,
                    origin_id TEXT REFERENCES network_stores(id), consulted INTEGER NOT NULL,
                    responded INTEGER NOT NULL, result_count INTEGER NOT NULL,
                    compatible_count INTEGER NOT NULL, failures INTEGER NOT NULL,
                    elapsed REAL NOT NULL, engine TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS network_results (
                    search_id TEXT NOT NULL REFERENCES network_searches(id),
                    match_key TEXT NOT NULL, store_id TEXT NOT NULL REFERENCES network_stores(id),
                    product_name TEXT NOT NULL, price TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(search_id, match_key)
                );
                CREATE TABLE IF NOT EXISTS network_events (
                    id TEXT PRIMARY KEY, search_id TEXT NOT NULL,
                    match_key TEXT NOT NULL, kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(search_id, match_key) REFERENCES network_results(search_id, match_key),
                    UNIQUE(search_id, match_key, kind)
                );
                CREATE TABLE IF NOT EXISTS network_referrals (
                    id TEXT PRIMARY KEY, search_id TEXT NOT NULL,
                    match_key TEXT NOT NULL, origin_id TEXT NOT NULL REFERENCES network_stores(id),
                    destination_id TEXT NOT NULL REFERENCES network_stores(id),
                    created_at TEXT NOT NULL, CHECK(origin_id <> destination_id),
                    FOREIGN KEY(search_id, match_key) REFERENCES network_results(search_id, match_key),
                    UNIQUE(search_id, match_key)
                );
                CREATE TABLE IF NOT EXISTS network_cache (
                    cache_key TEXT PRIMARY KEY, mode TEXT NOT NULL,
                    payload TEXT NOT NULL, expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS network_search_mode ON network_searches(mode,created_at);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_store(self, store: Store) -> None:
        check_mode(store.mode)
        if not store.id or not store.name.strip() or len(store.name) > 100:
            raise ValueError("Informe um nome de loja com até 100 caracteres.")
        if not store.consent_at or (store.mode == "real" and not store.endpoint_checked_at):
            raise ValueError("A loja precisa autorizar a participação e concluir a verificação da conexão.")
        if ((store.latitude is not None or store.longitude is not None)
                and not valid_coordinates(store.latitude, store.longitude)):
            raise ValueError("Informe latitude e longitude válidas, ou deixe ambas vazias.")
        if store.mode == "real":
            from coconutray.tray import _hostname
            if store.domain != "https://" + _hostname(store.domain):
                raise ValueError("Informe o domínio HTTPS canônico da loja.")
        try:
            with self.connection() as db:
                db.execute("""INSERT INTO network_stores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (store.id, store.name.strip(), store.domain, store.mode, store.address,
                            json.dumps(store.categories, ensure_ascii=False), store.latitude,
                            store.longitude, store.location_source, store.tray_name,
                            store.consent_at, store.endpoint_checked_at, int(store.active)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Esta loja já está cadastrada neste ambiente.") from exc

    def stores(self, mode: str, *, active_only: bool = True) -> list[Store]:
        check_mode(mode)
        with self.connection() as db:
            rows = db.execute("SELECT * FROM network_stores WHERE mode=?" +
                              (" AND active=1" if active_only else "") + " ORDER BY name", (mode,)).fetchall()
        return [Store(**{**dict(row), "categories": tuple(json.loads(row["categories"])),
                         "active": bool(row["active"])}) for row in rows]

    def set_active(self, store_id: str, active: bool) -> None:
        with self.connection() as db:
            db.execute("UPDATE network_stores SET active=? WHERE id=?", (int(active), store_id))

    def record_search(self, report: SearchReport, mode: str, origin_id: str | None = None) -> str:
        check_mode(mode)
        search_id = uuid4().hex
        with self.connection() as db:
            if origin_id:
                origin = db.execute("SELECT * FROM network_stores WHERE id=? AND mode=? AND active=1",
                                    (origin_id, mode)).fetchone()
                if not origin:
                    raise ValueError("Selecione uma loja de origem participante deste ambiente.")
            for match in report.matches:
                store = db.execute("SELECT mode FROM network_stores WHERE id=?", (match.store.id,)).fetchone()
                if match.store.mode != mode or not store or store["mode"] != mode:
                    raise ValueError("Os resultados devem pertencer ao mesmo ambiente da pesquisa.")
                if match.product.source != ("demo" if mode == "demo" else "tray"):
                    raise ValueError("A origem do produto deve corresponder ao ambiente da pesquisa.")
            db.execute("INSERT INTO network_searches VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (search_id, mode, now(), origin_id, report.consulted, report.responded,
                        len(report.matches), sum(m.status == "compatible" for m in report.matches),
                        len(report.failures), report.elapsed, report.intent.engine))
            db.executemany("INSERT INTO network_results VALUES (?,?,?,?,?,?)",
                           [(search_id, m.key, m.store.id, m.product.name, str(m.product.price), m.status)
                            for m in report.matches])
        return search_id

    def record_event(self, search_id: str, match_key: str, kind: str) -> str:
        if kind not in {"offer_click", "buy_link_requested", "cart_created"}:
            raise ValueError("Evento operacional inválido.")
        with self.connection() as db:
            result = db.execute("SELECT 1 FROM network_results WHERE search_id=? AND match_key=?",
                                (search_id, match_key)).fetchone()
            if not result:
                raise ValueError("A oferta não pertence a esta pesquisa.")
            db.execute("INSERT OR IGNORE INTO network_events VALUES (?,?,?,?,?)",
                       (uuid4().hex, search_id, match_key, kind, now()))
            return db.execute("SELECT id FROM network_events WHERE search_id=? AND match_key=? AND kind=?",
                              (search_id, match_key, kind)).fetchone()[0]

    def prepare_referral(self, search_id: str, match_key: str) -> str:
        with self.connection() as db:
            row = db.execute("""SELECT s.origin_id, r.store_id, s.mode FROM network_searches s
                JOIN network_results r ON s.id=r.search_id WHERE s.id=? AND r.match_key=?""",
                             (search_id, match_key)).fetchone()
            if not row or not row["origin_id"] or row["origin_id"] == row["store_id"]:
                raise ValueError("Escolha uma oferta de outra loja para preparar uma indicação.")
            db.execute("INSERT OR IGNORE INTO network_referrals VALUES (?,?,?,?,?,?)",
                       (uuid4().hex, search_id, match_key, row["origin_id"], row["store_id"], now()))
            return db.execute("SELECT id FROM network_referrals WHERE search_id=? AND match_key=?",
                              (search_id, match_key)).fetchone()[0]

    def metrics(self, mode: str) -> dict:
        check_mode(mode)
        with self.connection() as db:
            values = dict(db.execute("""SELECT count(*) searches,
                coalesce(sum(result_count>0),0) with_results, coalesce(sum(result_count=0),0) without_results,
                coalesce(sum(consulted),0) stores_consulted, coalesce(sum(failures),0) failures,
                coalesce(avg(elapsed),0) average_seconds FROM network_searches WHERE mode=?""", (mode,)).fetchone())
            values["referrals"] = db.execute("""SELECT count(*) FROM network_referrals r
                JOIN network_searches s ON s.id=r.search_id WHERE s.mode=?""", (mode,)).fetchone()[0]
            for kind in ("offer_click", "buy_link_requested", "cart_created"):
                values[kind] = db.execute("""SELECT count(*) FROM network_events e
                    JOIN network_searches s ON s.id=e.search_id WHERE s.mode=? AND e.kind=?""", (mode,kind)).fetchone()[0]
        return values

    def recent_referrals(self, mode: str) -> list[dict]:
        check_mode(mode)
        with self.connection() as db:
            return [dict(r) for r in db.execute("""SELECT r.created_at, a.name origin, b.name destination,
                p.product_name FROM network_referrals r JOIN network_searches s ON s.id=r.search_id
                JOIN network_stores a ON a.id=r.origin_id JOIN network_stores b ON b.id=r.destination_id
                JOIN network_results p ON p.search_id=r.search_id AND p.match_key=r.match_key
                WHERE s.mode=? ORDER BY r.created_at DESC LIMIT 20""", (mode,)).fetchall()]
