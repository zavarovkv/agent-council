"""Durable admission, task/session linkage, OAuth grants and idempotent publication."""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

JOB_FIELDS = frozenset({"state", "run_id", "output", "control", "updated", "error"})


def stable_id(value):
    return str(uuid.UUID(bytes=hashlib.sha256(value.encode()).digest()[:16], version=4))


class Store:
    def __init__(self, home, *, clock=time.time):
        self.path, self.clock = Path(home) / "linear.sqlite3", clock
        if self.path.resolve() != self.path:
            raise ValueError("Refusing a symlinked integration store.")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS installations (
                    agent TEXT PRIMARY KEY, app_user TEXT NOT NULL, organization TEXT NOT NULL,
                    access TEXT NOT NULL, refresh TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_states (
                    digest TEXT PRIMARY KEY, browser TEXT NOT NULL, agent TEXT NOT NULL,
                    verifier TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    agent TEXT NOT NULL, id TEXT NOT NULL, hermes TEXT NOT NULL,
                    issue TEXT NOT NULL, owner TEXT NOT NULL, depth INTEGER NOT NULL,
                    parent_agent TEXT, parent_session TEXT, updated REAL NOT NULL,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(agent,id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    key TEXT PRIMARY KEY, agent TEXT NOT NULL, session TEXT NOT NULL,
                    action TEXT NOT NULL, activity TEXT NOT NULL, actor TEXT NOT NULL,
                    text TEXT NOT NULL, created REAL NOT NULL, received REAL NOT NULL,
                    stop INTEGER NOT NULL, revision TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued', run_id TEXT NOT NULL DEFAULT '',
                    output TEXT NOT NULL DEFAULT '', control TEXT NOT NULL DEFAULT '',
                    updated REAL NOT NULL, error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS linear_jobs_session ON jobs(agent,session,received);
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, agent TEXT NOT NULL, session TEXT NOT NULL,
                    kind TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS delegations (
                    id TEXT PRIMARY KEY, parent_agent TEXT NOT NULL, parent_session TEXT NOT NULL,
                    child_agent TEXT NOT NULL, issue TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    description TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'reserved',
                    child_session TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
                    origin_job TEXT NOT NULL DEFAULT '',
                    notified INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS issue_operations (
                    id TEXT PRIMARY KEY, agent TEXT NOT NULL, session TEXT NOT NULL,
                    request TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    result TEXT NOT NULL DEFAULT '', created REAL NOT NULL
                );
            """)
            if "parent_issue" not in {
                row[1] for row in db.execute("PRAGMA table_info(delegations)")
            }:
                db.execute(
                    "ALTER TABLE delegations ADD COLUMN parent_issue TEXT NOT NULL DEFAULT ''"
                )
            self._prune(db)
        self.path.chmod(0o600)

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _prune(self, db):
        db.execute("DELETE FROM oauth_states WHERE expires<?", (self.clock(),))
        db.execute(
            "DELETE FROM jobs WHERE received<? AND state IN ('done','failed','cancelled')",
            (self.clock() - 7 * 86400,),
        )
        db.execute(
            "DELETE FROM outbox WHERE created<? AND state='sent'", (self.clock() - 7 * 86400,)
        )
        db.execute(
            "DELETE FROM delegations WHERE created<? AND state='done' AND notified=1",
            (self.clock() - 14 * 86400,),
        )
        db.execute(
            "DELETE FROM sessions WHERE updated<? AND NOT EXISTS(SELECT 1 FROM jobs WHERE jobs.agent=sessions.agent AND jobs.session=sessions.id)",
            (self.clock() - 30 * 86400,),
        )
        db.execute("DELETE FROM issue_operations WHERE created<?", (self.clock() - 30 * 86400,))

    def issue_operation(self, session, key, request):
        identity = stable_id(session["agent"] + ":" + session["id"] + ":issue:" + key)
        body = json.dumps(request, sort_keys=True, ensure_ascii=False)
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM issue_operations WHERE id=?", (identity,)).fetchone()
            if row:
                if row["request"] != body:
                    raise ValueError("Request key already belongs to a different issue operation.")
                return dict(row)
            if (
                db.execute(
                    "SELECT count(*) FROM issue_operations WHERE agent=? AND session=?",
                    (session["agent"], session["id"]),
                ).fetchone()[0]
                >= 100
            ):
                raise PermissionError("Issue operation limit reached for this session.")
            db.execute(
                "INSERT INTO issue_operations(id,agent,session,request,created) VALUES(?,?,?,?,?)",
                (identity, session["agent"], session["id"], body, self.clock()),
            )
            return dict(
                db.execute("SELECT * FROM issue_operations WHERE id=?", (identity,)).fetchone()
            )

    def claim_issue_operation(self, identity):
        with self.database() as db:
            return (
                db.execute(
                    "UPDATE issue_operations SET state='attempted' WHERE id=? AND state='pending'",
                    (identity,),
                ).rowcount
                == 1
            )

    def finish_issue_operation(self, identity, result):
        with self.database() as db:
            db.execute(
                "UPDATE issue_operations SET state='done',result=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), identity),
            )

    def install(self, agent, app_user, organization, access, refresh, expires):
        with self.database() as db:
            db.execute(
                "INSERT INTO installations VALUES(?,?,?,?,?,?) ON CONFLICT(agent) DO UPDATE SET app_user=excluded.app_user,organization=excluded.organization,access=excluded.access,refresh=excluded.refresh,expires=excluded.expires",
                (agent, app_user, organization, access, refresh, expires),
            )

    def installation(self, agent):
        with self.database() as db:
            row = db.execute("SELECT * FROM installations WHERE agent=?", (agent,)).fetchone()
            return dict(row) if row else None

    def oauth_state(self, state, browser, agent, verifier):
        with self.database() as db:
            self._prune(db)
            if db.execute("SELECT count(*) FROM oauth_states").fetchone()[0] >= 16:
                raise BlockingIOError("Too many setup attempts.")
            db.execute(
                "INSERT INTO oauth_states VALUES(?,?,?,?,?)",
                (
                    hashlib.sha256(state.encode()).hexdigest(),
                    hashlib.sha256(browser.encode()).hexdigest(),
                    agent,
                    verifier,
                    self.clock() + 600,
                ),
            )

    def consume_oauth(self, state, browser, agent):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            digest = hashlib.sha256(state.encode()).hexdigest()
            row = db.execute("SELECT * FROM oauth_states WHERE digest=?", (digest,)).fetchone()
            if (
                row is None
                or row["agent"] != agent
                or row["browser"] != hashlib.sha256(browser.encode()).hexdigest()
                or row["expires"] <= self.clock()
            ):
                raise PermissionError("Invalid or expired setup state.")
            db.execute("DELETE FROM oauth_states WHERE digest=?", (digest,))
            return row["verifier"]

    def admit(self, event, revision):
        now = self.clock()
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db)
            if db.execute("SELECT 1 FROM jobs WHERE key=?", (event.key,)).fetchone():
                return False
            if event.created < now - 86400 or event.created > now + 60:
                return False
            if (
                db.execute("SELECT count(*) FROM jobs WHERE received>?", (now - 3600,)).fetchone()[
                    0
                ]
                >= 60
                or db.execute(
                    "SELECT count(*) FROM jobs WHERE state NOT IN ('done','failed','cancelled')"
                ).fetchone()[0]
                >= 64
            ):
                raise BlockingIOError("Linear work capacity reached.")
            values = asdict(event)
            values.update(received=now, revision=revision, updated=now)
            db.execute(
                "INSERT INTO jobs(key,agent,session,action,activity,actor,text,created,received,stop,revision,updated) VALUES(:key,:agent,:session,:action,:activity,:actor,:text,:created,:received,:stop,:revision,:updated)",
                values,
            )
            return True

    def jobs(self):
        with self.database() as db:
            self._prune(db)
            return [
                dict(row)
                for row in db.execute(
                    "SELECT rowid AS sequence,* FROM jobs WHERE state NOT IN ('done','failed','cancelled') ORDER BY stop DESC,rowid"
                )
            ]

    def job(self, key):
        with self.database() as db:
            row = db.execute("SELECT rowid AS sequence,* FROM jobs WHERE key=?", (key,)).fetchone()
            return dict(row) if row else None

    def started(self, agent, sid, exclude):
        with self.database() as db:
            return (
                db.execute(
                    "SELECT 1 FROM jobs WHERE agent=? AND session=? AND key!=? AND run_id!=''",
                    (agent, sid, exclude),
                ).fetchone()
                is not None
            )

    def update_job(self, key, **fields):
        if not fields or not set(fields) <= JOB_FIELDS:
            raise ValueError("Invalid job fields.")
        fields["updated"] = self.clock()
        with self.database() as db:
            db.execute(
                "UPDATE jobs SET " + ",".join(name + "=?" for name in fields) + " WHERE key=?",
                (*fields.values(), key),
            )
            if fields.get("state") in {"done", "failed", "cancelled"}:
                db.execute("UPDATE jobs SET text='',control='' WHERE key=?", (key,))

    def session(self, agent, sid):
        with self.database() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE agent=? AND id=?", (agent, sid)
            ).fetchone()
            return dict(row) if row else None

    def bind(self, agent, sid, issue, owner, *, parent=None):
        with self.database() as db:
            existing = db.execute(
                "SELECT * FROM sessions WHERE agent=? AND id=?", (agent, sid)
            ).fetchone()
            if existing and (
                existing["blocked"] or (existing["issue"], existing["owner"]) != (issue, owner)
            ):
                raise PermissionError("Session identity changed.")
            hermes = "linear_" + stable_id(agent + ":" + sid).replace("-", "")
            db.execute(
                "INSERT INTO sessions(agent,id,hermes,issue,owner,depth,parent_agent,parent_session,updated) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(agent,id) DO UPDATE SET updated=excluded.updated",
                (
                    agent,
                    sid,
                    hermes,
                    issue,
                    owner,
                    1 if parent else 0,
                    parent["parent_agent"] if parent else None,
                    parent["parent_session"] if parent else None,
                    self.clock(),
                ),
            )
            if parent:
                db.execute(
                    "UPDATE delegations SET child_session=?,state='active' WHERE id=?",
                    (sid, parent["id"]),
                )
        return self.session(agent, sid)

    def publish(self, agent, sid, identity, kind, body):
        if (
            kind not in {"thought", "elicitation", "response", "error"}
            or not isinstance(body, str)
            or not 0 < len(body) <= 16000
        ):
            raise ValueError("Invalid outbound activity.")
        activity = stable_id(identity)
        with self.database() as db:
            existing = db.execute("SELECT * FROM outbox WHERE id=?", (activity,)).fetchone()
            if existing and (
                existing["agent"],
                existing["session"],
                existing["kind"],
                existing["body"],
            ) != (agent, sid, kind, body):
                raise ValueError("Activity identity conflict.")
            db.execute(
                "INSERT OR IGNORE INTO outbox(id,agent,session,kind,body,created) VALUES(?,?,?,?,?,?)",
                (activity, agent, sid, kind, body, self.clock()),
            )
        return activity

    def outbound(self):
        with self.database() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM outbox WHERE state IN ('pending','uncertain') ORDER BY created,id"
                )
            ]

    def published(self, identity):
        with self.database() as db:
            row = db.execute("SELECT state FROM outbox WHERE id=?", (identity,)).fetchone()
            return row is not None and row["state"] == "sent"

    def publication_exists(self, identity):
        with self.database() as db:
            return db.execute("SELECT 1 FROM outbox WHERE id=?", (identity,)).fetchone() is not None

    def context(self, agent, run, session):
        with self.database() as db:
            rows = db.execute(
                "SELECT sessions.*,jobs.key AS origin_job FROM sessions JOIN jobs ON jobs.agent=sessions.agent AND jobs.session=sessions.id WHERE sessions.agent=? AND sessions.blocked=0 AND jobs.state IN ('starting','running') AND ((jobs.run_id!='' AND jobs.run_id=?) OR sessions.hermes=?)",
                (agent, run, session),
            ).fetchall()
            if len(rows) != 1:
                raise PermissionError("No unique active Linear task owns this tool call.")
            return dict(rows[0])

    def cancel(self, agent, sid):
        with self.database() as db:
            db.execute("UPDATE sessions SET blocked=1 WHERE agent=? AND id=?", (agent, sid))
            db.execute(
                "UPDATE jobs SET state=CASE WHEN run_id!='' THEN 'cancelling' ELSE 'cancelled' END,text='',control='',updated=? WHERE agent=? AND session=? AND state NOT IN ('done','failed','cancelled')",
                (self.clock(), agent, sid),
            )
            db.execute(
                "UPDATE outbox SET state='cancelled' WHERE agent=? AND session=? AND state!='sent'",
                (agent, sid),
            )

    def notify_parent(self, parent, children, revision):
        # Admission and notification acknowledgment are one transaction, so a
        # restart cannot lose or duplicate the coordinator continuation.
        keys = sorted(child["id"] for child in children)
        key = hashlib.sha256((parent["id"] + ":children:" + ",".join(keys)).encode()).hexdigest()
        text = json.dumps(
            {
                "completed_sub_issues": [
                    {
                        "id": child["issue"],
                        "agent": child["child_agent"],
                        "result": child["result"][:8000],
                    }
                    for child in children
                ]
            },
            ensure_ascii=False,
        )
        now = self.clock()
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO jobs(key,agent,session,action,activity,actor,text,created,received,stop,revision,updated) VALUES(?,?,?,'children','',?,?,?,?,0,?,?)",
                (
                    key,
                    parent["agent"],
                    parent["id"],
                    parent["owner"],
                    text,
                    now,
                    now,
                    revision,
                    now,
                ),
            )
            db.execute(
                "UPDATE jobs SET state='done',text='',updated=? WHERE agent=? AND session=? AND state='waiting_children'",
                (now, parent["agent"], parent["id"]),
            )
            for child in children:
                db.execute("UPDATE delegations SET notified=1 WHERE id=?", (child["id"],))

    def publication(self, activity, *, confirmed):
        with self.database() as db:
            db.execute(
                "UPDATE outbox SET state=?,attempts=attempts+1 WHERE id=?",
                ("sent" if confirmed else "uncertain", activity),
            )

    def delegation(self, child_agent, issue):
        with self.database() as db:
            row = db.execute(
                "SELECT * FROM delegations WHERE child_agent=? AND issue=?", (child_agent, issue)
            ).fetchone()
            return dict(row) if row else None

    def parent_session(self, issue):
        with self.database() as db:
            rows = db.execute(
                "SELECT * FROM sessions WHERE issue=? AND depth=0 AND blocked=0", (issue,)
            ).fetchall()
            if len(rows) != 1:
                raise PermissionError(
                    "A unique authorized coordinator session must own the parent issue."
                )
            return dict(rows[0])

    def reserve_child(
        self, parent, target, key, title, description, *, issue=None, parent_issue=None
    ):
        parent_issue = parent_issue or parent["issue"]
        identity = stable_id(parent["agent"] + ":" + parent["id"] + ":" + key)
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM delegations WHERE id=?", (identity,)).fetchone()
            if row:
                if (row["child_agent"], row["title"], row["description"]) != (
                    target,
                    title,
                    description,
                ):
                    raise ValueError("Delegation identity conflict.")
                if (row["parent_issue"] or parent["issue"]) != parent_issue:
                    raise ValueError("Delegation parent conflict.")
                return dict(row)
            if (
                parent["depth"]
                or db.execute(
                    "SELECT count(*) FROM delegations WHERE parent_agent=? AND parent_session=? AND state!='done'",
                    (parent["agent"], parent["id"]),
                ).fetchone()[0]
                >= 4
            ):
                raise PermissionError(
                    "Delegation depth or four concurrent specialist tasks limit reached."
                )
            db.execute(
                "INSERT INTO delegations(id,parent_agent,parent_session,child_agent,issue,title,description,created,origin_job,parent_issue) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    parent["agent"],
                    parent["id"],
                    target,
                    issue or stable_id(identity + ":issue"),
                    title,
                    description,
                    self.clock(),
                    parent.get("origin_job", ""),
                    parent_issue,
                ),
            )
        return self.delegation(target, issue or stable_id(identity + ":issue"))

    def children(self, agent, sid):
        with self.database() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM delegations WHERE parent_agent=? AND parent_session=? ORDER BY created,id",
                    (agent, sid),
                )
            ]

    def child_finished(self, agent, sid, result):
        with self.database() as db:
            db.execute(
                "UPDATE delegations SET state='done',result=? WHERE child_agent=? AND child_session=?",
                (result[:16000], agent, sid),
            )

    def ready_children(self):
        with self.database() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM delegations WHERE state='done' AND notified=0 ORDER BY created,id"
                )
            ]

    def child_notified(self, identity):
        with self.database() as db:
            db.execute("UPDATE delegations SET notified=1 WHERE id=?", (identity,))

    def status(self):
        with self.database() as db:
            return {
                "jobs": dict(db.execute("SELECT state,count(*) FROM jobs GROUP BY state")),
                "outbox": dict(db.execute("SELECT state,count(*) FROM outbox GROUP BY state")),
                "children": dict(
                    db.execute("SELECT state,count(*) FROM delegations GROUP BY state")
                ),
            }
