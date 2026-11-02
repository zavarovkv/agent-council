"""Owner-authorized schedule admission using the existing Linear execution flow."""

import hashlib
import json
import re
import threading
from datetime import datetime

from council_recurring.contract import ZONE, definition, occurrence, revision

from .config import identifier, private_json
from .events import Event
from .store import stable_id
from .transport import RemoteError


class Recurring:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.lock = threading.RLock()
        with self.store.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS recurring_definitions (
                    id TEXT PRIMARY KEY, body TEXT NOT NULL, revision TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, owner TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recurring_runs (
                    key TEXT PRIMARY KEY, definition TEXT NOT NULL, day TEXT NOT NULL,
                    body TEXT NOT NULL, owner TEXT NOT NULL, policy TEXT NOT NULL,
                    issue TEXT NOT NULL UNIQUE, session TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'admitted', result TEXT NOT NULL DEFAULT '',
                    created REAL NOT NULL, updated REAL NOT NULL, deadline REAL NOT NULL,
                    error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
                    retry_at REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS recurring_deliveries (
                    run TEXT NOT NULL, kind TEXT NOT NULL, execution TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', body TEXT NOT NULL,
                    PRIMARY KEY(run,kind)
                );
            """)

    def grant(self):
        grant = private_json(self.service.home / "recurring.json")
        registry = self.service.registry()
        if (
            set(grant) != {"schema", "key", "owner"}
            or grant["schema"] != 1
            or not re.fullmatch(r"[a-f0-9]{64}", grant["key"])
            or grant["owner"] != registry.owner
        ):
            raise PermissionError("Recurring grant is invalid or revoked.")
        return grant

    def rows(self):
        with self.store.database() as db:
            return [
                dict(r)
                for r in db.execute("SELECT * FROM recurring_runs ORDER BY created DESC LIMIT 100")
            ]

    def run(self, key):
        with self.store.database() as db:
            row = db.execute("SELECT * FROM recurring_runs WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def update(self, key, **fields):
        allowed = {"session", "state", "result", "error", "attempts", "retry_at"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("Invalid recurring state update.")
        with self.store.database() as db:
            db.execute(
                "UPDATE recurring_runs SET "
                + ",".join(k + "=?" for k in fields)
                + ",updated=? WHERE key=?",
                (*fields.values(), self.service.clock(), key),
            )

    def authorized(self, registry, agent, issue, sid=None):
        if agent.slug != "qui-gon":
            return None
        with self.store.database() as db:
            row = db.execute("SELECT * FROM recurring_runs WHERE issue=?", (issue,)).fetchone()
        if (
            row
            and row["owner"] == registry.owner
            and row["policy"] == registry.revision
            and row["state"] != "cancelled"
            and (not sid or not row["session"] or row["session"] == sid)
        ):
            self.grant()
            return dict(row)
        return None

    def bind(self, key, sid):
        with self.store.database() as db:
            changed = db.execute(
                "UPDATE recurring_runs SET session=?,state=CASE WHEN state='admitted' THEN 'researching' ELSE state END,updated=? WHERE key=? AND (session='' OR session=?)",
                (sid, self.service.clock(), key, sid),
            ).rowcount
        if not changed:
            raise PermissionError("A different session already owns this occurrence.")

    def execute(self, payload):
        self.grant()
        registry = self.service.registry()
        action = payload.get("action")
        if action not in {"list", "status"} and not registry.enabled:
            raise BlockingIOError("Scheduled execution is disabled.")
        if (
            action not in {"list", "status", "configure"}
            and (self.service.home / "runtime/maintenance").exists()
        ):
            raise BlockingIOError("Scheduled admission is paused.")
        with self.lock:
            if action == "configure":
                value = definition(payload["definition"])
                if value["team"] not in registry.teams:
                    raise PermissionError("The team is outside the owner grant.")
                workflow = self.service.linear.workflow(registry.agents["qui-gon"], value["team"])
                if value["project"] and value["project"] not in {
                    p["id"] for p in workflow["projects"]["nodes"]
                }:
                    raise PermissionError("Select an accessible project in this team.")
                with self.store.database() as db:
                    db.execute(
                        "INSERT INTO recurring_definitions VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,revision=excluded.revision,enabled=1,owner=excluded.owner",
                        (
                            value["id"],
                            json.dumps(value, ensure_ascii=False),
                            revision(value),
                            1,
                            registry.owner,
                        ),
                    )
                return {"definition": value, "revision": revision(value)}
            if action == "list":
                with self.store.database() as db:
                    definitions = [
                        dict(r) for r in db.execute("SELECT * FROM recurring_definitions")
                    ]
                return {
                    "definitions": definitions,
                    "runs": [
                        {k: v for k, v in r.items() if k not in {"body", "owner", "policy"}}
                        for r in self.rows()
                        if r["owner"] == registry.owner and r["policy"] == registry.revision
                    ],
                }
            if action == "start":
                return self.start(payload, registry)
            if action == "status":
                run = self.run(payload["key"])
                if not run:
                    return {"state": "not_started", "key": payload["key"]}
                if run["owner"] != registry.owner or run["policy"] != registry.revision:
                    raise PermissionError("The occurrence grant was revoked.")
                with self.store.database() as db:
                    deliveries = [
                        dict(r)
                        for r in db.execute(
                            "SELECT * FROM recurring_deliveries WHERE run=?", (run["key"],)
                        )
                    ]
                return {
                    **run,
                    "result": json.loads(run["result"]) if run["result"] else None,
                    "deliveries": deliveries,
                }
            if action in {"claim", "settle"}:
                return self.delivery(payload)
            if action == "problem":
                return self.problem(payload, registry)
            raise ValueError("Unsupported recurring operation.")

    def start(self, payload, registry):
        with self.store.database() as db:
            row = db.execute(
                "SELECT * FROM recurring_definitions WHERE id=? AND enabled=1 AND owner=?",
                (payload["id"], registry.owner),
            ).fetchone()
        if not row:
            raise PermissionError("Schedule is not registered.")
        value = json.loads(row["body"])
        day = (
            payload.get("day")
            or datetime.fromtimestamp(self.service.clock(), ZONE).date().isoformat()
        )
        existing = self.run(value["id"] + ":" + day)
        if existing:
            if existing["owner"] != registry.owner or existing["policy"] != registry.revision:
                raise PermissionError("The occurrence grant was revoked.")
            return existing
        period = occurrence(value, day)
        # Backfills are explicit operator work; a routine cannot create an unbounded backlog.
        if (
            abs(
                datetime.fromtimestamp(self.service.clock(), ZONE).date().toordinal()
                - datetime.fromisoformat(day).date().toordinal()
            )
            > 1
        ):
            raise ValueError("Only the current or adjacent reporting date can be admitted.")
        with self.store.database() as db:
            db.execute(
                "INSERT OR IGNORE INTO recurring_runs(key,definition,day,body,owner,policy,issue,created,updated,deadline) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    period["key"],
                    value["id"],
                    day,
                    row["body"],
                    registry.owner,
                    registry.revision,
                    stable_id("recurring:" + period["key"] + ":" + registry.organization),
                    self.service.clock(),
                    self.service.clock(),
                    period["deadline"],
                ),
            )
        self.service.wake.set()
        return self.run(period["key"])

    def reconcile(self, registry):
        if (
            not (self.service.home / "recurring.json").exists()
            or not registry.enabled
            or (self.service.home / "runtime/maintenance").exists()
        ):
            return
        try:
            self.grant()
        except (OSError, ValueError, PermissionError):
            # A broken optional schedule grant must not stop unrelated Linear work.
            return
        agent = registry.agents["qui-gon"]
        installation = self.store.installation(agent.slug)
        if not installation or installation["organization"] != registry.organization:
            return
        with self.lock:
            for run in self.rows():
                if (
                    run["state"] == "delivered"
                    and run["session"]
                    and run["owner"] == registry.owner
                    and run["policy"] == registry.revision
                ):
                    self.followup(
                        run,
                        registry,
                        "delivery_confirmed",
                        "The native scheduler confirmed report delivery. Read the existing result and delivery status. Accept this low-risk completed task through linear_accept if allowed by acceptance-policy; leave owner-reserved work in review. Do not resend the report.",
                    )
                if (
                    run["session"]
                    or run["state"] in {"cancelled", "blocked"}
                    or run["retry_at"] > self.service.clock()
                ):
                    continue
                if run["policy"] != registry.revision or run["owner"] != registry.owner:
                    self.update(run["key"], state="cancelled", error="grant_changed")
                    continue
                if run["attempts"] >= 8 or self.service.clock() > run["deadline"] + 7200:
                    self.update(run["key"], state="blocked", error="admission_deadline")
                    continue
                self.update(
                    run["key"],
                    attempts=run["attempts"] + 1,
                    retry_at=self.service.clock() + min(300, 10 * 2 ** run["attempts"]),
                )
                try:
                    value = json.loads(run["body"])
                    period = occurrence(value, run["day"])
                    issue = self.service.linear.recurring_issue(agent, run["issue"])
                    if not issue:
                        self.service.linear.create_issue(
                            agent,
                            {
                                "id": run["issue"],
                                "teamId": value["team"],
                                "projectId": value["project"],
                                "delegateId": installation["app_user"],
                                "title": value["title"] + " · " + run["day"],
                                "description": self.brief(value, period),
                            },
                        )
                        issue = self.service.linear.recurring_issue(agent, run["issue"])
                    if (
                        not issue
                        or issue["team"]["id"] != value["team"]
                        or (issue.get("creator") or {}).get("id") != installation["app_user"]
                        or (issue.get("delegate") or {}).get("id") != installation["app_user"]
                    ):
                        raise PermissionError("Recurring issue identity conflict.")
                    sessions = [
                        s
                        for s in issue["agentSessions"]["nodes"]
                        if (s.get("appUser") or {}).get("id") == installation["app_user"]
                        and not s.get("archivedAt")
                        and not s.get("dismissedAt")
                    ]
                    if len(sessions) > 1:
                        raise PermissionError("Ambiguous recurring session.")
                    if sessions:
                        sid = identifier(sessions[0]["id"])
                        self.bind(run["key"], sid)
                        key = hashlib.sha256(f"{agent.slug}:{sid}:created:".encode()).hexdigest()
                        self.store.admit(
                            Event(
                                key,
                                agent.slug,
                                sid,
                                "created",
                                "",
                                installation["app_user"],
                                "",
                                self.service.clock(),
                            ),
                            registry.revision,
                        )
                except PermissionError:
                    self.update(run["key"], state="blocked", error="identity_conflict")
                except RemoteError:
                    self.update(run["key"], error="linear_unavailable")

    def problem(self, payload, registry):
        run = self.run(payload["key"])
        if not run or run["owner"] != registry.owner or run["policy"] != registry.revision:
            raise PermissionError("The occurrence grant was revoked.")
        category = payload.get("category")
        if category not in {"artifact_unverified", "deadline_exceeded"}:
            raise ValueError("Use a known recurring problem category.")
        self.update(run["key"], error=category)
        text = f"Scheduled occurrence {run['key']} in issue {run['issue']}: {category}. Inspect the existing task and artifact, return to In Progress before edits, resolve the blocker within the original scope and submit the corrected verified report through recurring_result. Reuse existing child tasks and cloud write receipts. Do not create another schedule or send Telegram messages. If an owner decision is needed, ask one specific question in Linear."
        self.followup(run, registry, category, text)
        return {"recorded": True}

    def followup(self, run, registry, category, text):
        if run["session"]:
            # Internal continuation, deliberately distinct from an owner prompt.
            key = hashlib.sha256(f"recurring:{run['key']}:{category}".encode()).hexdigest()
            installation = self.store.installation("qui-gon")
            if not installation:
                return
            self.store.admit(
                Event(
                    key,
                    "qui-gon",
                    run["session"],
                    "scheduled_followup",
                    "",
                    installation["app_user"],
                    text,
                    self.service.clock(),
                ),
                registry.revision,
            )
            self.service.wake.set()

    @staticmethod
    def brief(value, period):
        return f"""{value["brief"]}

Reporting occurrence: `{period["key"]}`.
Window: {period["window_start"]} → {period["window_end"]}.
Deliver by {value["delivery_time"]} Europe/Moscow. This is an owner-authorized recurring assignment.
Use Linear as the task tracker. Start the task, delegate research to Dexter with linear_delegate, verify his sources and result. Do not use native Kanban, shell launchers or research_archive. Missing optional Telegram evidence is a coverage gap; proceed with public sources.
Save the reviewed formatted report under Notion parent {value["notion_parent"]}. Include the exact occurrence marker `{period["key"]}` and reporting window. For publication/executive copy request Padme review; apply the existing acceptance policy.
Submit the task for review with its verified Notion URL. Then call recurring_result with the Notion page ID/revision and a concise Russian summary (maximum 700 characters). This registers a candidate; delivery independently verifies the saved artifact. Do not send Telegram messages: the schedule owns delivery. If blocked, record one concrete next action and the responsible agent in Linear. No new schedules or credentials are needed.
"""

    def submit(self, slug, payload):
        if slug != "qui-gon":
            raise PermissionError("Only the coordinator submits a reviewed edition.")
        session = self.store.context(slug, payload["run"], payload["session"])
        registry = self.service.registry()
        _, remote = self.service.authorize(registry, registry.agents[slug], session["id"])
        run = self.authorized(registry, registry.agents[slug], remote["issue"]["id"], session["id"])
        if not run:
            raise PermissionError("This is not a scheduled coordinator session.")
        from .issues import is_review

        issue = self.service.linear.issue_details(registry.agents[slug], run["issue"])
        if not is_review(issue["state"]) and issue["state"]["type"] != "completed":
            raise ValueError("Submit the task for review before registering its deliverable.")
        summary = payload.get("summary")
        if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 700:
            raise ValueError("Use a concise summary of at most 700 characters.")
        page = payload.get("page", "").replace("-", "")
        if not re.fullmatch(r"[a-f0-9]{32}", page) or not re.fullmatch(
            r"[a-f0-9]{64}", payload.get("revision", "")
        ):
            raise ValueError("Use the page ID and exact revision from council_workspace readback.")
        result = {"page": page, "revision": payload["revision"], "summary": summary.strip()}
        with self.lock:
            current = self.run(run["key"])
            if current["result"] and json.loads(current["result"]) != result:
                with self.store.database() as db:
                    claimed = db.execute(
                        "SELECT 1 FROM recurring_deliveries WHERE run=? AND kind='report'",
                        (run["key"],),
                    ).fetchone()
                if claimed:
                    raise ValueError("Reconcile the existing report delivery before replacing it.")
            self.update(
                run["key"],
                state="reviewed",
                result=json.dumps(result, ensure_ascii=False),
                error="",
            )
        return {
            "registered": True,
            "key": run["key"],
            "delivery": "pending_independent_verification",
        }

    def delivery(self, payload):
        run = self.run(payload["key"])
        registry = self.service.registry()
        if (
            not run
            or run["owner"] != registry.owner
            or run["policy"] != registry.revision
            or payload.get("kind") not in {"report", "alert"}
            or not re.fullmatch(r"[a-f0-9]{32}", payload.get("execution", ""))
        ):
            raise ValueError("A real occurrence, delivery kind and native execution are required.")
        identity = (run["key"], payload["kind"])
        with self.store.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM recurring_deliveries WHERE run=? AND kind=?", identity
            ).fetchone()
            if payload["action"] == "settle":
                if payload.get("outcome") not in {"delivered", "failed", "unknown"}:
                    raise ValueError("Invalid native delivery outcome.")
                if row and row["execution"] == payload["execution"] and row["state"] == "pending":
                    db.execute(
                        "UPDATE recurring_deliveries SET state=? WHERE run=? AND kind=?",
                        (payload["outcome"], *identity),
                    )
                    if payload["kind"] == "report" and payload["outcome"] == "delivered":
                        db.execute(
                            "UPDATE recurring_runs SET state='delivered',updated=? WHERE key=?",
                            (self.service.clock(), run["key"]),
                        )
                return {"recorded": True}
            body = payload.get("body")
            if not isinstance(body, str) or not 1 <= len(body) <= 1800:
                raise ValueError("A bounded prepared message is required.")
            if row:
                if row["execution"] == payload["execution"] and row["state"] == "pending":
                    return {"body": row["body"]}
                if row["state"] != "failed":
                    return {"body": ""}
                db.execute("DELETE FROM recurring_deliveries WHERE run=? AND kind=?", identity)
            db.execute(
                "INSERT INTO recurring_deliveries(run,kind,execution,body) VALUES(?,?,?,?)",
                (*identity, payload["execution"], body),
            )
            return {"body": body}
