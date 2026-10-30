"""Transactional deduplication, cost limits and delivered-only per-context history."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

HOURLY_LIMITS = {"business": 60, "public": 40}


class History:
    def __init__(self, home, *, clock=time.time):
        self.path = Path(home) / "secretary-history.sqlite3"
        self.clock = clock
        if self.path.is_symlink():
            raise ValueError("Refusing a symlinked history store.")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS turns (
                    request_id TEXT PRIMARY KEY, conversation TEXT NOT NULL,
                    question TEXT NOT NULL, answer TEXT, created REAL NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS turns_conversation ON turns(conversation,created);
            """)
            # Older reservations conservatively consume the public budget. Never
            # infer a privileged route from user text or a mutable username.
            if "route" not in {row[1] for row in db.execute("PRAGMA table_info(turns)")}:
                db.execute("ALTER TABLE turns ADD COLUMN route TEXT NOT NULL DEFAULT 'public'")
            db.execute("CREATE INDEX IF NOT EXISTS turns_route ON turns(route,created)")
            db.execute("DELETE FROM turns WHERE created < ?", (clock() - 14 * 86400,))
        self.path.chmod(0o600)

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def begin(self, request):
        """Reserve once; a committed reservation survives crashes without repeating spend."""
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.clock()
            db.execute("DELETE FROM turns WHERE created < ?", (now - 14 * 86400,))
            previous = db.execute(
                "SELECT conversation,question,answer,route FROM turns WHERE request_id=?",
                (request.identifier,),
            ).fetchone()
            if previous:
                if previous[:2] != (request.context, request.text) or previous[3] != request.route:
                    raise ValueError("Request identity conflict.")
                if previous[2] is None:
                    raise BlockingIOError("Request already in progress or interrupted.")
                if (
                    not isinstance(previous[2], str)
                    or not previous[2].strip()
                    or len(previous[2]) > 3000
                ):
                    raise RuntimeError("Invalid stored reply.")
                return previous[2], []
            recent = db.execute(
                "SELECT count(*) FROM turns WHERE conversation=? AND created>?",
                (request.context, now - 3600),
            ).fetchone()[0]
            total = db.execute(
                "SELECT count(*) FROM turns WHERE route=? AND created>?",
                (request.route, now - 3600),
            ).fetchone()[0]
            if recent >= 20 or total >= HOURLY_LIMITS[request.route]:
                raise BlockingIOError("Hourly reply limit reached.")
            history = db.execute(
                "SELECT question,answer FROM turns WHERE conversation=? AND delivered=1 "
                "ORDER BY created DESC,rowid DESC LIMIT 8",
                (request.context,),
            ).fetchall()[::-1]
            db.execute(
                "INSERT INTO turns(request_id,conversation,question,answer,created,route) "
                "VALUES (?,?,?,?,?,?)",
                (request.identifier, request.context, request.text, None, now, request.route),
            )
        return None, history

    def complete(self, identifier, answer):
        with self.database() as db:
            db.execute("UPDATE turns SET answer=? WHERE request_id=?", (answer, identifier))

    def acknowledge(self, identifier):
        with self.database() as db:
            db.execute(
                "UPDATE turns SET delivered=1 WHERE request_id=? AND answer IS NOT NULL",
                (identifier,),
            )
