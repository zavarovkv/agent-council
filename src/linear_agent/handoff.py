"""Immutable, authorized result transport; public summaries are a separate surface."""

import hashlib
import json
import re
from urllib.parse import urlsplit

from .store import stable_id

PAGE = 6000
LEGACY_LIMIT = 128000
FINAL = {"done", "failed", "cancelled"}


def bounded(value, limit, name, *, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"Invalid {name}: limit {limit} characters.")
    return value


def request_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value):
        raise ValueError(
            "A stable key of 1–80 letters, digits, underscores or hyphens is required."
        )
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def submission(payload):
    if set(payload) - {
        "action",
        "run",
        "session",
        "key",
        "kind",
        "summary",
        "details",
        "artifacts",
        "limitations",
        "next_action",
    }:
        raise ValueError("Unsupported handoff fields; sender and recipient are server-derived.")
    request_key(payload.get("key"))
    if payload.get("kind") not in {"result", "blocked"}:
        raise ValueError("Handoff kind must be result or blocked.")
    result = {"kind": payload["kind"]}
    for name, limit in (
        ("summary", 500),
        ("details", 24000),
        ("limitations", 500),
        ("next_action", 300),
    ):
        result[name] = bounded(
            payload.get(name, ""),
            limit,
            name,
            required=name == "summary" or (name == "next_action" and result["kind"] == "blocked"),
        )
    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list) or len(artifacts) > 8:
        raise ValueError("At most eight artifacts are allowed.")
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"url", "version"}:
            raise ValueError("Each artifact requires url and version.")
        url = bounded(artifact["url"], 2048, "artifact URL", required=True)
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or any(char.isspace() or ord(char) < 32 for char in url)
        ):
            raise ValueError("Artifact URLs must be HTTPS without credentials or whitespace.")
        bounded(artifact["version"], 160, "artifact version", required=True)
    result["artifacts"] = artifacts
    if result["kind"] == "result" and not (result["details"].strip() or artifacts):
        raise ValueError("A result requires full details or a versioned artifact.")
    display = result["summary"]
    for name, label in (("limitations", "Ограничения"), ("next_action", "Следующий шаг")):
        if result[name]:
            display += f"\n{label}: {result[name]}"
    bounded(display, 700, "rendered public message", required=True)
    result["display"] = display
    # Keep every accepted metadata envelope readable alongside a worst-case
    # JSON-escaped page within the executor client's 64 KiB response boundary.
    metadata = {key: value for key, value in result.items() if key not in {"details", "display"}}
    if len(canonical(metadata).encode()) > 24000:
        raise ValueError("Handoff metadata exceeds 24000 encoded bytes; use fewer artifact links.")
    return result


class Handoffs:
    def __init__(self, service):
        self.service, self.store = service, service.store

    def for_job(self, job_key):
        with self.store.database() as db:
            row = db.execute("SELECT * FROM handoffs WHERE origin_job=?", (job_key,)).fetchone()
            return self.decode(row)

    @staticmethod
    def decode(row):
        return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    def save(self, session, key, payload):
        body = canonical(payload)
        digest = hashlib.sha256(body.encode()).hexdigest()
        identity = stable_id(session["origin_job"] + ":handoff:" + key)
        with self.store.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM handoffs WHERE origin_job=?", (session["origin_job"],)
            ).fetchone()
            if row:
                if row["request_key"] != key or row["payload"] != body:
                    raise ValueError(
                        "This run already has an immutable handoff; retry the identical request."
                    )
                return self.decode(row)
            if (
                db.execute(
                    "SELECT count(*) FROM handoffs WHERE agent=? AND session=?",
                    (session["agent"], session["id"]),
                ).fetchone()[0]
                >= 128
                or db.execute("SELECT count(*) FROM handoffs").fetchone()[0] >= 4096
            ):
                raise ValueError(
                    "Handoff storage quota reached; no result was truncated or accepted."
                )
            db.execute(
                "INSERT INTO handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    session["origin_job"],
                    session["agent"],
                    session["id"],
                    session["issue"],
                    session["parent_agent"],
                    session["parent_session"],
                    key,
                    body,
                    digest,
                    self.store.clock(),
                ),
            )
            return self.decode(
                db.execute("SELECT * FROM handoffs WHERE id=?", (identity,)).fetchone()
            )

    def legacy(self, session, job, output, *, failed=False):
        existing = self.for_job(job["key"])
        if existing:
            return existing
        bounded(output, LEGACY_LIMIT, "executor result", required=True)
        display = (
            output
            if len(output) <= 700 and not failed
            else (
                "Исполнение не завершено. Причина остановки и доступные данные сохранены для координатора."
                if failed
                else "Рабочие материалы сохранены для проверки."
            )
        )
        return self.save(
            {**session, "origin_job": job["key"]},
            "legacy-result",
            {
                "kind": "blocked" if failed else "result",
                "summary": display,
                "display": display,
                "details": output,
                "artifacts": [],
                "limitations": "",
                "next_action": "Проверьте частичный результат и причину остановки."
                if failed
                else "",
            },
        )

    def execute(self, slug, payload):
        registry = self.service.registry()
        agent = registry.agents[slug]
        session = self.store.context(slug, payload.get("run"), payload.get("session"))
        job = self.store.job(session["origin_job"])
        if (
            not job
            or job["revision"] != registry.revision
            or (self.service.home / "runtime/maintenance").exists()
        ):
            raise PermissionError("This task's execution policy changed.")
        self.service.authorize(registry, agent, session["id"])
        action = payload.get("action")
        if action == "submit":
            event = self.save(session, request_key(payload.get("key")), submission(payload))
            return {
                "event_id": event["id"],
                "digest": event["digest"],
                "stored": True,
                "published": False,
                "instruction": "Finish this turn; the adapter will route this handoff when execution completes.",
            }
        allowed = {"action", "run", "session", "event_id"} | (
            {"offset", "view"} if action == "get" else {"key", "message"}
        )
        if action not in {"get", "resume"} or set(payload) - allowed:
            raise ValueError("Invalid handoff action or fields.")
        event_id = payload.get("event_id")
        if not isinstance(event_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", event_id):
            raise ValueError("Invalid handoff event_id.")
        with self.store.database() as db:
            event = self.decode(
                db.execute("SELECT * FROM handoffs WHERE id=?", (event_id,)).fetchone()
            )
        if not event:
            raise ValueError("Handoff not found or retention expired.")
        own = (slug, session["id"]) == (event["agent"], event["session"])
        parent = (slug, session["id"]) == (event["parent_agent"], event["parent_session"])
        if not own and not parent:
            raise PermissionError("This handoff does not belong to the current task tree.")
        self.service.authorize(registry, registry.agents[event["agent"]], event["session"])
        origin = self.store.job(event["origin_job"])
        if parent and (not origin or origin["state"] not in FINAL):
            raise ValueError(
                "The child run has not settled; its submitted handoff is not a result yet."
            )
        if action == "resume":
            if not parent or session["depth"]:
                raise PermissionError("Only this child's coordinator can resume it.")
            return self.resume(registry, session, event, payload)
        view = payload.get("view", "details")
        if view not in {"details", "checkpoint"}:
            raise ValueError("Handoff view must be details or checkpoint.")
        checkpoint = (
            origin["output"] if origin and origin["state"] in {"failed", "cancelled"} else ""
        )
        if view == "checkpoint" and not checkpoint:
            raise ValueError("No settled execution checkpoint is available.")
        details = checkpoint if view == "checkpoint" else event["payload"]["details"]
        offset = payload.get("offset", 0)
        if type(offset) is not int or not 0 <= offset <= len(details):
            raise ValueError("Invalid details offset.")
        end = min(offset + PAGE, len(details))
        return {
            "event_id": event["id"],
            "revision": event["id"] + (":checkpoint" if view == "checkpoint" else ""),
            "digest": event["digest"],
            "view": view,
            "content_digest": hashlib.sha256(details.encode()).hexdigest(),
            "checkpoint_available": bool(checkpoint),
            "checkpoint_chars": len(checkpoint),
            "sender": event["agent"],
            "issue_id": event["issue"],
            "run_state": origin["state"] if origin else "expired",
            **{k: v for k, v in event["payload"].items() if k not in {"details", "display"}},
            "kind": "blocked"
            if origin and origin["state"] in {"failed", "cancelled"}
            else event["payload"]["kind"],
            "submitted_kind": event["payload"]["kind"],
            "execution_error": origin["error"] if origin else "",
            "details": details[offset:end],
            "offset": offset,
            "total_chars": len(details),
            "next_offset": end if end < len(details) else None,
        }

    def resume(self, registry, parent, event, payload):
        key = request_key(payload.get("key"))
        message = bounded(payload.get("message"), 2000, "resume message", required=True)
        identity = stable_id(event["id"] + ":resume:" + key)
        with self.store.database() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM handoff_resumes WHERE event_id=?", (event["id"],)
            ).fetchone()
            if previous:
                if previous["request_key"] != key or previous["message"] != message:
                    raise ValueError("This blocker already has a different resume request.")
                return {"event_id": event["id"], "queued": True, "job_key": previous["job_key"]}
            child = db.execute(
                "SELECT * FROM delegations WHERE child_agent=? AND child_session=?",
                (event["agent"], event["session"]),
            ).fetchone()
            if (
                not child
                or child["state"] != "blocked"
                or child["result_key"] != event["origin_job"]
                or (child["parent_agent"], child["parent_session"])
                != (parent["agent"], parent["id"])
            ):
                raise ValueError("Only the latest blocked child result can be resumed.")
            if db.execute(
                "SELECT 1 FROM jobs WHERE agent=? AND session=? AND state NOT IN ('done','failed','cancelled')",
                (event["agent"], event["session"]),
            ).fetchone():
                raise ValueError("The child already has pending work.")
            if (
                db.execute(
                    "SELECT count(*) FROM jobs WHERE state NOT IN ('done','failed','cancelled')"
                ).fetchone()[0]
                >= 64
            ):
                raise BlockingIOError("Linear work capacity reached.")
            text = canonical({"coordinator_reply": {"event_id": event["id"], "message": message}})
            now = self.store.clock()
            db.execute(
                "INSERT INTO jobs(key,agent,session,action,activity,actor,text,created,received,stop,revision,updated) VALUES(?,?,?,'coordinator_reply','',?,?,?,?,0,?,?)",
                (
                    identity,
                    event["agent"],
                    event["session"],
                    parent["owner"],
                    text,
                    now,
                    now,
                    registry.revision,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO handoff_resumes VALUES(?,?,?,?)", (event["id"], key, message, identity)
            )
            db.execute("UPDATE delegations SET state='active' WHERE id=?", (child["id"],))
        self.service.wake.set()
        return {"event_id": event["id"], "queued": True, "job_key": identity}
