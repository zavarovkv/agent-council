"""Durable write receipts: a lost response never repeats a cloud mutation."""

import hashlib
import json
import re
import sqlite3
from contextlib import closing


class Store:
    def __init__(self, path):
        self.path = path
        if path.resolve() != path:
            raise ValueError("Refusing a symlinked workspace receipt store.")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS writes(client TEXT,key TEXT,digest TEXT,result TEXT,PRIMARY KEY(client,key))"
            )
        path.chmod(0o600)

    @staticmethod
    def previous(db, client, key, digest):
        row = db.execute(
            "SELECT digest,result FROM writes WHERE client=? AND key=?", (client, key)
        ).fetchone()
        if not row:
            return None
        if row[0] != digest:
            raise ValueError("A write key cannot be reused for different content.")
        if row[1] is None:
            raise ValueError(
                "Previous write outcome is uncertain. Inspect the cloud material before any new write; this request will not be replayed."
            )
        return row[1]

    def write(self, client, request, prepare):
        """Prepare with reads only, then reserve once before the first cloud mutation."""
        key = request.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key):
            raise ValueError("Writes require a stable key; reuse it when checking a lost response.")
        digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            prior = self.previous(db, client, key, digest)
            if prior is not None:
                return json.loads(prior)
        # Invalid input, a stale revision or an unavailable read has not attempted
        # a mutation. Do not turn it into an unrecoverable uncertain-write receipt.
        # Preparation stays outside a database transaction and is skipped on replay.
        perform = prepare()
        if not callable(perform):
            raise ValueError("Write preparation must return a mutation.")
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            # Another process can have reserved or completed this key during
            # preparation. Only the winner may call the cloud mutation.
            prior = self.previous(db, client, key, digest)
            if prior is not None:
                return json.loads(prior)
            db.execute("INSERT INTO writes VALUES(?,?,?,NULL)", (client, key, digest))
        result = perform()
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute(
                "UPDATE writes SET result=? WHERE client=? AND key=?",
                (json.dumps(result, ensure_ascii=False), client, key),
            )
        return result
