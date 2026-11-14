"""Local, privacy-preserving operational metrics.

Only counts, timings, execution modes and technical error categories are stored.
Conversations, customer details and cart URLs never enter this database.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Iterator
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _count(value: int, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} deve ser um inteiro não negativo.")
    return value


def _mode(value: str) -> str:
    if value not in {"demo", "real"}:
        raise ValueError("O modo deve ser 'demo' ou 'real'.")
    return value


def _category(value: str, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
        raise ValueError(f"{field} deve conter apenas uma categoria técnica, sem dados pessoais.")
    return value


class Database:
    """A thread-safe SQLite connection; transactions keep each event atomic."""

    def __init__(self, path: str | Path = "data/coconutray.sqlite3") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 15000")
        self._initialize()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connection:
            yield self._connection

    def _initialize(self) -> None:
        with self._transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise ValueError("Este banco foi criado por uma versão mais recente da Coconutray.")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL CHECK (mode IN ('demo', 'real')),
                    started_at TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'started'
                        CHECK (state IN ('started', 'analysed', 'cart_created', 'checkout_forwarded'))
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    created_at TEXT NOT NULL,
                    duration_seconds REAL NOT NULL CHECK (duration_seconds >= 0),
                    product_count INTEGER NOT NULL CHECK (product_count >= 0),
                    missing_count INTEGER NOT NULL CHECK (missing_count >= 0),
                    corrections INTEGER NOT NULL DEFAULT 0 CHECK (corrections >= 0),
                    matching_errors INTEGER NOT NULL DEFAULT 0 CHECK (matching_errors >= 0)
                );
                CREATE TABLE IF NOT EXISTS cart_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    created_at TEXT NOT NULL,
                    success INTEGER NOT NULL CHECK (success IN (0, 1)),
                    integration TEXT NOT NULL CHECK (integration IN ('demo', 'tray', 'real')),
                    cart_ref TEXT,
                    checkout_forwarded INTEGER NOT NULL DEFAULT 0
                        CHECK (checkout_forwarded IN (0, 1)),
                    checkout_at TEXT,
                    CHECK (success = 1 OR checkout_forwarded = 0)
                );
                CREATE TABLE IF NOT EXISTS integration_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    service TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_mode ON sessions(mode);
                CREATE INDEX IF NOT EXISTS idx_analyses_session ON analyses(session_id);
                CREATE INDEX IF NOT EXISTS idx_carts_session ON cart_events(session_id);
                CREATE INDEX IF NOT EXISTS idx_errors_session ON integration_errors(session_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_cart_success_reference
                    ON cart_events(session_id, cart_ref) WHERE success = 1 AND cart_ref IS NOT NULL;
                PRAGMA user_version = 1;
            """)

    @staticmethod
    def _session(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        session = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None:
            raise ValueError("Sessão de operação desconhecida.")
        return session

    def start_session(self, mode: str) -> str:
        mode = _mode(mode)
        session_id = str(uuid4())
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO sessions(id, mode, started_at) VALUES (?, ?, ?)",
                (session_id, mode, _now()),
            )
        return session_id

    def record_analysis(
        self,
        session_id: str,
        duration_seconds: float,
        product_count: int,
        missing_count: int,
        corrections: int = 0,
        matching_errors: int = 0,
    ) -> int:
        if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)):
            raise ValueError("A duração deve ser um número não negativo.")
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise ValueError("A duração deve ser um número finito não negativo.")
        values = (
            _count(product_count, "Produtos"), _count(missing_count, "Pendências"),
            _count(corrections, "Correções"), _count(matching_errors, "Erros de correspondência"),
        )
        with self._transaction() as connection:
            self._session(connection, session_id)
            cursor = connection.execute(
                """INSERT INTO analyses(session_id, created_at, duration_seconds, product_count,
                   missing_count, corrections, matching_errors) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, _now(), float(duration_seconds), *values),
            )
            connection.execute("UPDATE sessions SET state = 'analysed' WHERE id = ?", (session_id,))
            return int(cursor.lastrowid)

    def record_corrections(self, analysis_id: int, corrections: int) -> None:
        """Set the latest reviewed correction count; rerenders do not accumulate it."""
        _count(corrections, "Correções")
        with self._transaction() as connection:
            cursor = connection.execute("UPDATE analyses SET corrections = ? WHERE id = ?", (corrections, analysis_id))
            if cursor.rowcount != 1:
                raise ValueError("Análise desconhecida.")

    def record_cart(
        self,
        session_id: str,
        success: bool,
        integration: str,
        cart_id: str | None = None,
    ) -> int:
        if type(success) is not bool:
            raise ValueError("O resultado do carrinho deve ser verdadeiro ou falso.")
        if integration not in {"demo", "tray", "real"}:
            raise ValueError("Integração de carrinho desconhecida.")
        if cart_id is not None and (not isinstance(cart_id, str) or not cart_id.strip()):
            raise ValueError("Identificador de carrinho inválido.")
        cart_ref = hashlib.sha256(cart_id.encode("utf-8")).hexdigest() if cart_id else None
        with self._transaction() as connection:
            session = self._session(connection, session_id)
            if (session["mode"] == "demo") != (integration == "demo"):
                raise ValueError("A integração do carrinho não corresponde ao modo da sessão.")
            if success and cart_ref is not None:
                previous = connection.execute(
                    "SELECT id FROM cart_events WHERE session_id = ? AND cart_ref = ? AND success = 1",
                    (session_id, cart_ref),
                ).fetchone()
                if previous is not None:
                    return int(previous["id"])
            cursor = connection.execute(
                "INSERT INTO cart_events(session_id, created_at, success, integration, cart_ref) VALUES (?, ?, ?, ?, ?)",
                (session_id, _now(), int(success), integration, cart_ref),
            )
            if success:
                connection.execute("UPDATE sessions SET state = 'cart_created' WHERE id = ?", (session_id,))
            return int(cursor.lastrowid)

    def record_checkout(self, event_id: int) -> None:
        """Record a handoff once; a handoff never implies a completed purchase."""
        with self._transaction() as connection:
            event = connection.execute("SELECT * FROM cart_events WHERE id = ?", (event_id,)).fetchone()
            if event is None:
                raise ValueError("Evento de carrinho desconhecido.")
            if not event["success"]:
                raise ValueError("Um carrinho com falha não pode ser encaminhado ao checkout.")
            connection.execute(
                """UPDATE cart_events SET checkout_forwarded = 1, checkout_at = COALESCE(checkout_at, ?)
                   WHERE id = ?""",
                (_now(), event_id),
            )
            connection.execute(
                "UPDATE sessions SET state = 'checkout_forwarded' WHERE id = ?", (event["session_id"],)
            )

    def record_error(self, session_id: str, service: str, error_type: str) -> None:
        service = _category(service, "Serviço")
        error_type = _category(error_type, "Tipo de erro")
        with self._transaction() as connection:
            self._session(connection, session_id)
            connection.execute(
                "INSERT INTO integration_errors(session_id, service, error_type, created_at) VALUES (?, ?, ?, ?)",
                (session_id, service, error_type, _now()),
            )

    def metrics(self, mode: str) -> dict:
        mode = _mode(mode)
        with self._lock:
            analyses = self._connection.execute(
                """SELECT COUNT(*) AS conversations, COALESCE(AVG(a.duration_seconds), 0) AS average_seconds,
                   COALESCE(SUM(a.corrections), 0) AS corrections,
                   COALESCE(SUM(a.matching_errors), 0) AS matching_errors
                   FROM analyses a JOIN sessions s ON s.id = a.session_id WHERE s.mode = ?""", (mode,)
            ).fetchone()
            carts = self._connection.execute(
                """SELECT COALESCE(SUM(c.success), 0) AS carts,
                   COALESCE(SUM(c.checkout_forwarded), 0) AS checkouts
                   FROM cart_events c JOIN sessions s ON s.id = c.session_id WHERE s.mode = ?""", (mode,)
            ).fetchone()
            errors = self._connection.execute(
                """SELECT COUNT(*) FROM integration_errors e JOIN sessions s ON s.id = e.session_id
                   WHERE s.mode = ?""", (mode,)
            ).fetchone()[0]
            return {**dict(analyses), **dict(carts), "integration_errors": errors}

    def recent_activity(self, mode: str, limit: int = 8) -> list[dict]:
        mode = _mode(mode)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("O limite de atividades deve estar entre 1 e 100.")
        with self._lock:
            rows = self._connection.execute(
                """SELECT date, event, status, session_id FROM (
                    SELECT a.created_at AS date, 'analysis' AS event, 'completed' AS status, a.session_id
                      FROM analyses a JOIN sessions s ON s.id = a.session_id WHERE s.mode = ?
                    UNION ALL
                    SELECT c.created_at, 'cart', CASE WHEN c.success = 1 THEN 'completed' ELSE 'failed' END,
                           c.session_id FROM cart_events c JOIN sessions s ON s.id = c.session_id WHERE s.mode = ?
                    UNION ALL
                    SELECT c.checkout_at, 'checkout', 'forwarded', c.session_id
                      FROM cart_events c JOIN sessions s ON s.id = c.session_id
                      WHERE s.mode = ? AND c.checkout_forwarded = 1
                    UNION ALL
                    SELECT e.created_at, 'integration_error', e.error_type, e.session_id
                      FROM integration_errors e JOIN sessions s ON s.id = e.session_id WHERE s.mode = ?
                ) ORDER BY date DESC LIMIT ?""", (mode, mode, mode, mode, limit)
            ).fetchall()
            return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
