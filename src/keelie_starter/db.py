"""The website's own records, in SQLite. Keelie never sees the email, the password or the session.

Each user points at one Keelie subject. Each subscription, consent, top-up and credential the
user starts is kept here, so every id the browser sends can be checked against its owner.
A credential's secret is never stored: only its id and client id.

SQLite keeps the starter to one file. Swap this module for your own database: the pages only
call the methods below.
"""

import secrets
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    subject_id TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id),
    csrf TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id TEXT NOT NULL REFERENCES users(id),
    plan_id TEXT NOT NULL,
    subscription_id TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, plan_id)
);
CREATE TABLE IF NOT EXISTS consents (
    subscription_id TEXT NOT NULL,
    consent_ref TEXT NOT NULL,
    agreed_at REAL NOT NULL,
    PRIMARY KEY (subscription_id, consent_ref)
);
CREATE TABLE IF NOT EXISTS top_ups (
    subscription_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    credits INTEGER NOT NULL,
    balance_before TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (subscription_id, n)
);
CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    subscription_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

SESSION_SECONDS = 8 * 3600


@dataclass(frozen=True)
class User:
    id: str
    email: str
    password_hash: str
    display_name: str
    subject_key: str
    subject_id: str | None


@dataclass(frozen=True)
class Session:
    token: str
    user_id: str | None
    csrf: str


@dataclass(frozen=True)
class SubscriptionLink:
    user_id: str
    plan_id: str
    subscription_id: str


@dataclass(frozen=True)
class TopUp:
    subscription_id: str
    n: int
    credits: int
    balance_before: str


@dataclass(frozen=True)
class CredentialLink:
    id: str
    user_id: str
    subscription_id: str
    client_id: str


def _user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        display_name=row["display_name"],
        subject_key=row["subject_key"],
        subject_id=row["subject_id"],
    )


def _link(row: sqlite3.Row) -> SubscriptionLink:
    return SubscriptionLink(row["user_id"], row["plan_id"], row["subscription_id"])


def _credential(row: sqlite3.Row) -> CredentialLink:
    return CredentialLink(row["id"], row["user_id"], row["subscription_id"], row["client_id"])


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        # An in-memory database lives as long as its connection, so keep one open for it.
        self._memory = sqlite3.connect(path) if path == ":memory:" else None
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = self._memory or sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        finally:
            if self._memory is None:
                db.close()

    # Sessions. A visitor gets one before logging in, so the login form has a CSRF token too.

    def session(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE token = ? AND created_at > ?",
                (token, time.time() - SESSION_SECONDS),
            ).fetchone()
        return Session(row["token"], row["user_id"], row["csrf"]) if row else None

    def new_session(self, user_id: str | None, old_token: str | None = None) -> Session:
        """A new session, and a new token. Call it at every login and logout."""
        session = Session(secrets.token_urlsafe(32), user_id, secrets.token_urlsafe(32))
        with self.connect() as db:
            if old_token:
                db.execute("DELETE FROM sessions WHERE token = ?", (old_token,))
            db.execute(
                "INSERT INTO sessions (token, user_id, csrf, created_at) VALUES (?, ?, ?, ?)",
                (session.token, user_id, session.csrf, time.time()),
            )
        return session

    # Users.

    def create_user(
        self, email: str, password_hash: str, display_name: str, namespace: uuid.UUID
    ) -> User:
        user_id = str(uuid.uuid4())
        # The subject's Idempotency-Key is stored with the user, before Keelie is called (prompt 02).
        subject_key = str(uuid.uuid5(namespace, f"subject:{user_id}"))
        with self.connect() as db:
            db.execute(
                "INSERT INTO users (id, email, password_hash, display_name, subject_key, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, email, password_hash, display_name, subject_key, time.time()),
            )
        return User(user_id, email, password_hash, display_name, subject_key, None)

    def user(self, user_id: str) -> User | None:
        with self.connect() as db:
            return _user(db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    def user_by_email(self, email: str) -> User | None:
        with self.connect() as db:
            return _user(db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())

    def set_subject(self, user_id: str, subject_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE users SET subject_id = ? WHERE id = ?", (subject_id, user_id))

    # Subscriptions, and the check that one belongs to the user.

    def add_subscription(self, user_id: str, plan_id: str, subscription_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO subscriptions (user_id, plan_id, subscription_id, created_at)"
                " VALUES (?, ?, ?, ?)",
                (user_id, plan_id, subscription_id, time.time()),
            )

    def subscriptions(self, user_id: str) -> list[SubscriptionLink]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at", (user_id,)
            ).fetchall()
        return [_link(row) for row in rows]

    def owned_subscription(self, user_id: str, subscription_id: str) -> SubscriptionLink | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM subscriptions WHERE user_id = ? AND subscription_id = ?",
                (user_id, subscription_id),
            ).fetchone()
        return _link(row) if row else None

    def add_consent(self, subscription_id: str, consent_ref: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO consents (subscription_id, consent_ref, agreed_at)"
                " VALUES (?, ?, ?)",
                (subscription_id, consent_ref, time.time()),
            )

    # Top-ups: one row for each purchase started, numbered, so the key comes from the action.

    def top_up_count(self, subscription_id: str) -> int:
        with self.connect() as db:
            row = db.execute(
                "SELECT count(*) FROM top_ups WHERE subscription_id = ?", (subscription_id,)
            ).fetchone()
        return int(row[0])

    def start_top_up(self, subscription_id: str, n: int, credits: int, balance_before: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO top_ups"
                " (subscription_id, n, credits, balance_before, created_at) VALUES (?, ?, ?, ?, ?)",
                (subscription_id, n, credits, balance_before, time.time()),
            )

    def top_up(self, subscription_id: str, n: int) -> TopUp | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM top_ups WHERE subscription_id = ? AND n = ?", (subscription_id, n)
            ).fetchone()
        if row is None:
            return None
        return TopUp(row["subscription_id"], row["n"], row["credits"], row["balance_before"])

    # Credentials: the id and the client id only. Never the secret.

    def credential_count(self, subscription_id: str) -> int:
        with self.connect() as db:
            row = db.execute(
                "SELECT count(*) FROM credentials WHERE subscription_id = ?", (subscription_id,)
            ).fetchone()
        return int(row[0])

    def add_credential(
        self, credential_id: str, user_id: str, subscription_id: str, client_id: str
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO credentials"
                " (id, user_id, subscription_id, client_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (credential_id, user_id, subscription_id, client_id, time.time()),
            )

    def credentials(self, user_id: str, subscription_id: str) -> list[CredentialLink]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM credentials WHERE user_id = ? AND subscription_id = ?"
                " ORDER BY created_at",
                (user_id, subscription_id),
            ).fetchall()
        return [_credential(row) for row in rows]

    def owned_credential(self, user_id: str, credential_id: str) -> CredentialLink | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM credentials WHERE user_id = ? AND id = ?", (user_id, credential_id)
            ).fetchone()
        return _credential(row) if row else None
