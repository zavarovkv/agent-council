"""Revocable application sessions; never retain Telegram tokens in the store."""

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import closing
from dataclasses import dataclass

PREFIX = "council_session_"
TOKEN = re.compile(r"council_session_[A-Za-z0-9_-]{43}\Z")
IDENTITY_FIELDS = ("sub", "id", "iss", "preferred_username", "name", "email", "org_id", "groups")


class SessionStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredSession:
    claims: dict
    expires_at: int


class SessionStore:
    def __init__(self, path, audience, *, days=30, clock=time.time):
        if not 1 <= days <= 90:
            raise ValueError("Session lifetime must be 1–90 days.")
        self.path = path
        self.audience = audience
        self.ttl = days * 86400
        self.clock = clock

    @staticmethod
    def _digest(token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return None
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def issue(self, claims):
        token = PREFIX + secrets.token_urlsafe(32)
        now = int(self.clock())
        expires = now + self.ttl
        identity = {name: claims[name] for name in IDENTITY_FIELDS if name in claims}
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                    raise OSError("Unexpected session store owner or file type.")
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, audience TEXT NOT NULL,
                    identity TEXT NOT NULL, expires INTEGER NOT NULL)""")
                db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
                if db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= 512:
                    raise SessionStoreError("Session capacity reached.")
                db.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                    (self._digest(token), self.audience, json.dumps(identity), expires),
                )
        except (OSError, sqlite3.Error) as exc:
            raise SessionStoreError("Session store unavailable.") from exc
        return token, StoredSession(identity, expires)

    def lookup(self, token):
        digest = self._digest(token)
        if digest is None:
            return None
        try:
            if not self.path.exists():
                return None
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute(
                    "SELECT identity, expires FROM sessions WHERE digest=? AND audience=?",
                    (digest, self.audience),
                ).fetchone()
            if row is None or row[1] <= int(self.clock()):
                return None
            claims = json.loads(row[0])
            if not isinstance(claims, dict) or claims.get("iss") != "https://oauth.telegram.org":
                raise ValueError("Invalid stored identity.")
            return StoredSession(claims, row[1])
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            raise SessionStoreError("Session store unavailable.") from exc

    def revoke(self, token):
        digest = self._digest(token)
        if digest is None:
            return
        try:
            if not self.path.exists():
                return
            with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                db.execute(
                    "DELETE FROM sessions WHERE digest=? AND audience=?", (digest, self.audience)
                )
        except (OSError, sqlite3.Error) as exc:
            raise SessionStoreError("Session store unavailable.") from exc
