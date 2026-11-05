"""Durable owner requests from the trusted native assistant, acted on as Qui-Gon."""

import hashlib
import json
import re
import threading

from council_team.communication import check_brief

from .config import identifier, private_json
from .events import Event
from .store import stable_id
from .transport import RemoteError


class Intake:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.lock = threading.RLock()
        with self.store.database() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS owner_tasks (
                key TEXT PRIMARY KEY, issue TEXT NOT NULL UNIQUE, body TEXT NOT NULL,
                owner TEXT NOT NULL, policy TEXT NOT NULL, session TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0
            )""")

    def grant(self):
        value = private_json(self.service.home / "native-tasks.json", limit=8192)
        registry = self.service.registry()
        if (
            set(value) != {"schema", "key", "owner", "team", "project"}
            or value["schema"] != 1
            or not isinstance(value["key"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["key"])
            or value["owner"] != registry.owner
            or value["team"] not in registry.teams
        ):
            raise PermissionError("Native task grant is invalid or revoked.")
        if value["project"] is not None:
            identifier(value["project"])
        return value

    def row(self, key=None, *, issue=None):
        with self.store.database() as db:
            row = db.execute(
                "SELECT * FROM owner_tasks WHERE " + ("issue=?" if issue else "key=?"),
                (issue or key,),
            ).fetchone()
        return dict(row) if row else None

    def authorized(self, registry, agent, issue, sid=None):
        if agent.slug != "qui-gon":
            return None
        row = self.row(issue=issue)
        if (
            not row
            or row["owner"] != registry.owner
            or row["policy"] != registry.revision
            or row["state"] == "blocked"
            or (sid and row["session"] and row["session"] != sid)
        ):
            return None
        grant = self.grant()
        body = json.loads(row["body"])
        if body["team"] != grant["team"] or body["project"] != grant["project"]:
            return None
        return row

    def bind(self, key, sid):
        with self.store.database() as db:
            changed = db.execute(
                "UPDATE owner_tasks SET session=?,state='started',error='' WHERE key=? AND (session='' OR session=?)",
                (sid, key, sid),
            ).rowcount
        if not changed:
            raise PermissionError("A different session owns this request.")

    def execute(self, payload):
        grant, registry = self.grant(), self.service.registry()
        action = payload.get("action")
        if not registry.enabled or (
            (self.service.home / "runtime/maintenance").exists() and action not in {"list", "get"}
        ):
            raise BlockingIOError("Native task admission is paused.")
        if action == "list" and set(payload) == {"action"}:
            with self.store.database() as db:
                rows = db.execute(
                    "SELECT key,issue,body,state,error FROM owner_tasks WHERE owner=? AND policy=? ORDER BY created DESC LIMIT 30",
                    (registry.owner, registry.revision),
                ).fetchall()
            return {
                "tasks": [
                    {
                        "key": r["key"],
                        "issue": r["issue"],
                        "state": r["state"],
                        "error": r["error"],
                        "title": json.loads(r["body"])["title"],
                        "url": "https://linear.app/issue/" + r["issue"],
                    }
                    for r in rows
                    if json.loads(r["body"])["team"] == grant["team"]
                    and json.loads(r["body"])["project"] == grant["project"]
                ]
            }
        key = payload.get("key", "")
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key):
            raise ValueError("Use a stable request key.")
        if action == "create" and set(payload) == {"action", "key", "title", "description"}:
            for name, limit in (("title", 200), ("description", 12000)):
                if (
                    not isinstance(payload[name], str)
                    or not 1 <= len(payload[name].strip()) <= limit
                ):
                    raise ValueError("Use a bounded task brief.")
            body = {name: payload[name].strip() for name in ("title", "description")}
            body.update(team=grant["team"], project=grant["project"])
            encoded = json.dumps(body, sort_keys=True, ensure_ascii=False)
            with self.lock, self.store.database() as db:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute("SELECT * FROM owner_tasks WHERE key=?", (key,)).fetchone()
                if previous and (
                    previous["body"] != encoded
                    or previous["owner"] != registry.owner
                    or previous["policy"] != registry.revision
                ):
                    raise PermissionError(
                        "Existing task key differs; inspect it before proceeding."
                    )
                if not previous:
                    check_brief(payload["description"])
                db.execute(
                    "INSERT OR IGNORE INTO owner_tasks(key,issue,body,owner,policy,created) VALUES(?,?,?,?,?,?)",
                    (
                        key,
                        stable_id("owner-task:" + registry.organization + ":" + key),
                        encoded,
                        registry.owner,
                        registry.revision,
                        self.service.clock(),
                    ),
                )
            self.service.wake.set()
            return self.describe(self.row(key), registry)
        row = self.row(key)
        if (
            not row
            or row["owner"] != registry.owner
            or row["policy"] != registry.revision
            or json.loads(row["body"])["team"] != grant["team"]
            or json.loads(row["body"])["project"] != grant["project"]
        ):
            raise PermissionError("This request is not authorized.")
        if action == "get" and set(payload) == {"action", "key"}:
            return self.describe(row, registry)
        if action == "reply" and set(payload) == {"action", "key", "message_key", "text"}:
            if not self.authorized(registry, registry.agents["qui-gon"], row["issue"]):
                raise PermissionError("This request is not authorized for execution.")
            if not row["session"]:
                raise BlockingIOError("Wait for the coordinator session.")
            if (
                not isinstance(payload["text"], str)
                or not 1 <= len(payload["text"].strip()) <= 8000
            ):
                raise ValueError("Use a bounded owner clarification.")
            if not isinstance(payload["message_key"], str) or not re.fullmatch(
                r"[a-zA-Z0-9_-]{1,80}", payload["message_key"]
            ):
                raise ValueError("Use a stable clarification key.")
            self.service.authorize(registry, registry.agents["qui-gon"], row["session"])
            identity = hashlib.sha256(
                ("native-reply:" + key + ":" + payload["message_key"]).encode()
            ).hexdigest()
            previous = self.store.job(identity)
            # Completed jobs discard their prompt. Retain the receipt separately
            # so retries remain idempotent after completion or transcript pruning.
            request = self.store.issue_operation(
                {"agent": "qui-gon", "id": row["session"]},
                "native-reply:" + payload["message_key"],
                {
                    "request": key,
                    "text_sha256": hashlib.sha256(payload["text"].encode()).hexdigest(),
                },
                retained=True,
            )
            if previous and previous["text"] and previous["text"] != payload["text"]:
                raise ValueError("Clarification key was already used for different text.")
            if request["state"] == "done":
                return {"accepted": False, "url": "https://linear.app/issue/" + row["issue"]}
            actor = self.store.installation("qui-gon")["app_user"]
            accepted = self.store.admit(
                Event(
                    identity,
                    "qui-gon",
                    row["session"],
                    "native_reply",
                    "",
                    actor,
                    payload["text"],
                    self.service.clock(),
                ),
                registry.revision,
            )
            if accepted or self.store.job(identity):
                self.store.finish_issue_operation(request["id"], {"accepted": True})
            self.service.wake.set()
            return {"accepted": accepted, "url": "https://linear.app/issue/" + row["issue"]}
        raise ValueError("Use create, list, get or reply with the documented fields.")

    def describe(self, row, registry):
        result = {
            "key": row["key"],
            "issue": row["issue"],
            "state": row["state"],
            "error": row["error"],
            "url": "https://linear.app/issue/" + row["issue"],
            "coordinator": "qui-gon",
            "human_assignee": None,
        }
        if row["session"]:
            issue = self.service.linear.task_status(registry.agents["qui-gon"], row["issue"])
            if not issue or issue["team"]["id"] != json.loads(row["body"])["team"]:
                raise PermissionError("The task moved outside its admission team.")
            result.update(
                status=issue["state"],
                title=issue["title"],
                human_assignee=issue.get("assignee"),
                delegate=issue.get("delegate"),
                work_status=self.service.task_status.describe(issue),
            )
            with self.store.database() as db:
                job = db.execute(
                    "SELECT state,error,output FROM jobs WHERE agent='qui-gon' AND session=? ORDER BY received DESC LIMIT 1",
                    (row["session"],),
                ).fetchone()
            if job:
                result["execution"] = {**dict(job), "output": job["output"][-4000:]}
            result["specialists"] = [
                {k: c[k] for k in ("child_agent", "issue", "state")}
                for c in self.store.children("qui-gon", row["session"])
            ]
        return result

    def reconcile(self, registry):
        if not registry.enabled or (self.service.home / "runtime/maintenance").exists():
            return
        try:
            grant = self.grant()
        except (OSError, ValueError, PermissionError):
            return
        agent = registry.agents["qui-gon"]
        installed = self.store.installation(agent.slug)
        if not installed or installed["organization"] != registry.organization:
            return
        with self.lock:
            with self.store.database() as db:
                rows = [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM owner_tasks WHERE session='' AND state='queued' AND retry_at<=? ORDER BY created LIMIT 20",
                        (self.service.clock(),),
                    )
                ]
            for row in rows:
                if not self.authorized(registry, agent, row["issue"]):
                    continue
                error = ""
                try:
                    body = json.loads(row["body"])
                    workflow = self.service.linear.workflow(agent, grant["team"])
                    if grant["project"] and grant["project"] not in {
                        p["id"] for p in workflow["projects"]["nodes"]
                    }:
                        raise PermissionError("Project grant changed.")
                    issue = self.service.linear.recurring_issue(agent, row["issue"])
                    if not issue:
                        self.service.linear.create_issue(
                            agent,
                            {
                                "id": row["issue"],
                                "teamId": body["team"],
                                "projectId": body["project"],
                                "assigneeId": None,
                                "delegateId": installed["app_user"],
                                "title": body["title"],
                                "description": body["description"],
                            },
                        )
                        issue = self.service.linear.recurring_issue(agent, row["issue"])
                    if (
                        not issue
                        or issue["team"]["id"] != body["team"]
                        or (issue.get("creator") or {}).get("id") != installed["app_user"]
                        or (issue.get("delegate") or {}).get("id") != installed["app_user"]
                    ):
                        raise PermissionError("Coordinator issue identity conflict.")
                    sessions = [
                        s
                        for s in issue["agentSessions"]["nodes"]
                        if (s.get("appUser") or {}).get("id") == installed["app_user"]
                        and not s.get("archivedAt")
                        and not s.get("dismissedAt")
                    ]
                    if len(sessions) > 1:
                        raise PermissionError("Ambiguous coordinator session.")
                    if sessions:
                        sid = identifier(sessions[0]["id"])
                        key = hashlib.sha256(f"{agent.slug}:{sid}:created:".encode()).hexdigest()
                        self.store.admit(
                            Event(
                                key,
                                agent.slug,
                                sid,
                                "created",
                                "",
                                installed["app_user"],
                                "",
                                self.service.clock(),
                            ),
                            registry.revision,
                        )
                        self.bind(row["key"], sid)
                        continue
                    error = "awaiting_linear_session"
                except PermissionError:
                    error = "identity_conflict"
                except RemoteError:
                    error = "linear_unavailable"
                except BlockingIOError:
                    error = "capacity_unavailable"
                except (KeyError, ValueError, TypeError):
                    error = "protocol_error"
                with self.store.database() as db:
                    db.execute(
                        "UPDATE owner_tasks SET attempts=attempts+1,retry_at=?,error=?,state=? WHERE key=? AND session=''",
                        (
                            self.service.clock() + min(300, 10 * 2 ** min(row["attempts"], 5)),
                            error,
                            "blocked"
                            if error == "identity_conflict" or row["attempts"] >= 7
                            else "queued",
                            row["key"],
                        ),
                    )
