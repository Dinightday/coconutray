"""Local merchant identities, opaque sessions and store ownership. No buyer login."""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from time import time


class MerchantAuth:
    def __init__(self, repository):
        self.repository = repository
        with repository.connection() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS merchant_accounts (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                salt BLOB NOT NULL, password_hash BLOB NOT NULL);
              CREATE TABLE IF NOT EXISTS merchant_sessions (
                token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES merchant_accounts(id),
                csrf TEXT NOT NULL, expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS merchant_stores (
                store_id TEXT PRIMARY KEY REFERENCES network_stores(id),
                account_id TEXT NOT NULL REFERENCES merchant_accounts(id));
              CREATE TABLE IF NOT EXISTS widget_settings (
                store_id TEXT PRIMARY KEY REFERENCES network_stores(id), theme TEXT NOT NULL);
            """)

    @staticmethod
    def _password(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)

    def register(self, username: str, password: str):
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.-]{3,50}", username):
            raise ValueError("Use de 3 a 50 letras, números, pontos ou traços no seu usuário.")
        if not 10 <= len(password) <= 128:
            raise ValueError("Escolha uma senha com 10 a 128 caracteres.")
        salt = secrets.token_bytes(16)
        try:
            with self.repository.connection() as db:
                db.execute("INSERT INTO merchant_accounts VALUES (?,?,?,?)",
                           (secrets.token_hex(16), username, salt, self._password(password, salt)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Este usuário já está cadastrado.") from exc

    def login(self, username: str, password: str):
        with self.repository.connection() as db:
            row = db.execute("SELECT * FROM merchant_accounts WHERE username=?", (username.strip().lower(),)).fetchone()
            digest = self._password(password, bytes(row["salt"]) if row else b"0" * 16)
            if not row or not hmac.compare_digest(digest, bytes(row["password_hash"])):
                raise ValueError("Usuário ou senha incorretos.")
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute("DELETE FROM merchant_sessions WHERE expires<?", (time(),))
            db.execute("INSERT INTO merchant_sessions VALUES (?,?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), row["id"], csrf, time() + 12 * 3600))
            return token, {"id":row["id"], "username":row["username"], "csrf":csrf}

    def session(self, token):
        if not token or len(token) > 200:
            return None
        with self.repository.connection() as db:
            row = db.execute("""SELECT a.id,a.username,s.csrf FROM merchant_sessions s
                JOIN merchant_accounts a ON a.id=s.account_id WHERE s.token_hash=? AND s.expires>?""",
                (hashlib.sha256(token.encode()).hexdigest(), time())).fetchone()
        return dict(row) if row else None

    def logout(self, token):
        with self.repository.connection() as db:
            db.execute("DELETE FROM merchant_sessions WHERE token_hash=?", (hashlib.sha256((token or "").encode()).hexdigest(),))

    def owns(self, user_id, store_id):
        with self.repository.connection() as db:
            return bool(db.execute("SELECT 1 FROM merchant_stores WHERE account_id=? AND store_id=?", (user_id, store_id)).fetchone())

    def assign(self, user_id, store_id):
        with self.repository.connection() as db:
            db.execute("INSERT INTO merchant_stores VALUES (?,?)", (store_id, user_id))

    def store_ids(self, user_id):
        with self.repository.connection() as db:
            return [r[0] for r in db.execute("SELECT store_id FROM merchant_stores WHERE account_id=?", (user_id,)).fetchall()]

    def theme(self, store_id):
        with self.repository.connection() as db:
            row = db.execute("SELECT theme FROM widget_settings WHERE store_id=?", (store_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_theme(self, store_id, theme):
        with self.repository.connection() as db:
            db.execute("INSERT OR REPLACE INTO widget_settings VALUES (?,?)", (store_id, json.dumps(theme)))
