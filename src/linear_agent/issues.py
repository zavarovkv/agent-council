"""Owner-authorized tracker operations with bounded team scope and durable receipts."""

import json
import re
from datetime import date

from .config import identifier
from .project_scope import ProjectScope
from .store import stable_id
from .transport import RemoteError

FIELDS = {
    "title",
    "description",
    "priority",
    "stateId",
    "projectId",
    "parentId",
    "assigneeId",
    "labelIds",
    "dueDate",
}


def first_state(workflow, category):
    states = [s for s in workflow["states"]["nodes"] if s["type"] == category]
    if not states:
        raise ValueError("The team has no workflow state of the requested type.")
    return min(states, key=lambda s: (s["position"], s["id"]))


def matches(issue, fields):
    for name, value in fields.items():
        if name in {"stateId", "projectId", "parentId", "assigneeId"}:
            actual = (issue.get(name[:-2]) or {}).get("id")
        elif name == "labelIds":
            actual = sorted(s["id"] for s in issue["labels"]["nodes"])
            value = sorted(value)
        else:
            actual = issue.get(name)
            if name == "description" and actual is None:
                actual = ""
        if actual != value:
            return False
    return True


def receipt(issue):
    return {
        k: issue.get(k)
        for k in ("id", "identifier", "title", "url", "state", "priority", "project", "assignee")
    }


class Issues:
    def __init__(self, service):
        self.service = service
        self.linear, self.store = service.linear, service.store

    def checked_issue(self, agent, registry, reference):
        if not isinstance(reference, str) or not (
            re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,19}-[1-9][0-9]{0,9}", reference)
            or re.fullmatch(r"[a-f0-9-]{36}", reference)
        ):
            raise ValueError("Use an immutable issue ID or a Linear issue identifier.")
        issue = self.linear.issue_details(agent, reference)
        if not issue or issue["team"]["id"] not in registry.teams or issue.get("archivedAt"):
            raise PermissionError("The issue is outside the configured active teams.")
        return issue

    def validate_fields(self, agent, registry, workflow, fields):
        if not isinstance(fields, dict) or not fields or not set(fields) <= FIELDS:
            raise ValueError("Unsupported issue fields.")
        for name, limit in (("title", 200), ("description", 12000)):
            if name in fields and (
                not isinstance(fields[name], str)
                or len(fields[name]) > limit
                or (name == "title" and not fields[name].strip())
            ):
                raise ValueError("Invalid issue text.")
        if "priority" in fields and (
            type(fields["priority"]) is not int or not 0 <= fields["priority"] <= 4
        ):
            raise ValueError("Priority must be an integer from zero to four.")
        for name, connection in (
            ("stateId", "states"),
            ("projectId", "projects"),
            ("assigneeId", "members"),
        ):
            if name not in fields:
                continue
            value = fields[name]
            if value is None and name != "stateId":
                continue
            identifier(value)
            candidates = workflow[connection]["nodes"]
            if name == "assigneeId":
                candidates = [c for c in candidates if c["active"] and not c["app"]]
            if value not in {c["id"] for c in candidates}:
                raise PermissionError(
                    "Select a visible state, project or human member of this team."
                )
        if "labelIds" in fields:
            labels = fields["labelIds"]
            if (
                not isinstance(labels, list)
                or len(labels) > 30
                or any(
                    identifier(label) not in {s["id"] for s in workflow["labels"]["nodes"]}
                    for label in labels
                )
            ):
                raise ValueError("Select labels from this team's metadata.")
        if fields.get("parentId"):
            parent = self.checked_issue(agent, registry, identifier(fields["parentId"]))
            if parent["team"]["id"] != workflow["id"]:
                raise PermissionError("Use a parent in the same team.")
        if "dueDate" in fields and fields["dueDate"] is not None:
            if date.fromisoformat(fields["dueDate"]).isoformat() != fields["dueDate"]:
                raise ValueError("Use an ISO calendar date.")

    def execute(self, slug, payload):
        if not set(payload) <= {
            "run",
            "session",
            "action",
            "issue",
            "team",
            "query",
            "cursor",
            "key",
            "fields",
            "body",
            "result",
            "verification",
            "offset",
            "document",
        }:
            raise ValueError("Unsupported issue operation parameters.")
        registry = self.service.registry()
        agent = registry.agents[slug]
        session = self.store.context(slug, payload["run"], payload["session"])
        job = self.store.job(session["origin_job"])
        if (
            not job
            or job["revision"] != registry.revision
            or (self.service.home / "runtime/maintenance").exists()
        ):
            raise PermissionError("This task's execution policy changed.")
        _, remote = self.service.authorize(registry, agent, session["id"])
        action = payload.get("action")
        coordinator = slug == "qui-gon" and not session["depth"]
        reference = payload.get("issue") or session["issue"]
        current = self.checked_issue(agent, registry, session["issue"])
        scope = None if coordinator else ProjectScope(self, agent, registry, current)
        team = payload.get("team") or remote["issue"]["team"]["id"]
        if team not in registry.teams:
            raise PermissionError("Select a configured Linear team.")
        if not coordinator and not scope.project and team != remote["issue"]["team"]["id"]:
            raise PermissionError("Specialists use only their task's team metadata.")
        if action == "context":
            return {
                "current_issue": session["issue"],
                "allowed_teams": sorted(registry.teams),
                "coordinator": coordinator,
                "shared_project": scope.project
                if scope
                else (current.get("project") or {}).get("id"),
                "shared_task_root": scope.root if scope else current["id"],
                "workflow": self.linear.workflow(agent, team),
            }
        if action == "list":
            query, cursor = payload.get("query", ""), payload.get("cursor")
            if (
                not isinstance(query, str)
                or len(query) > 200
                or (cursor is not None and (not isinstance(cursor, str) or len(cursor) > 512))
            ):
                raise ValueError("Invalid bounded issue search.")
            return self.linear.issues(
                agent, {team}, query=query, cursor=cursor, scope=scope.filter() if scope else None
            )
        issue = None if action == "create" else self.checked_issue(agent, registry, reference)
        if scope and issue:
            scope.require(issue)
        if action == "save_document":
            return self.save_document(agent, registry, session, issue, payload)
        if action in {"project", "document", "comments"}:
            return self.materials(agent, registry, issue, payload)
        if action == "get":
            # Keep the tool response bounded even when historical comments are large.
            issue.update(
                self.excerpt(
                    issue.get("description") or "", payload.get("offset", 0), "description"
                )
            )
            for comment in issue["comments"]["nodes"]:
                comment["body"] = (comment.get("body") or "")[:2000]
            return issue
        if action not in {"create", "update", "comment", "start", "complete"}:
            raise ValueError("Unknown issue action.")
        key = payload.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key):
            raise ValueError("Mutations require a stable request key.")
        if issue:
            team = issue["team"]["id"]
        workflow = self.linear.workflow(agent, team)
        fields = payload.get("fields", {})
        if not isinstance(fields, dict):
            raise ValueError("Issue fields must be an object.")
        fields = dict(fields)
        if scope:
            scope.fields(fields, creating=action == "create")
        if issue and fields.get("parentId") == issue["id"]:
            raise ValueError("An issue cannot be its own parent.")
        if action == "create":
            fields.setdefault("stateId", first_state(workflow, "backlog")["id"])
            if "title" not in fields:
                raise ValueError("An ordinary issue needs a title.")
        elif action == "start":
            fields = {"stateId": first_state(workflow, "started")["id"]}
        elif action == "complete":
            for name, limit in (("result", 500), ("verification", 160)):
                if (
                    not isinstance(payload.get(name), str)
                    or not 1 <= len(payload[name].strip()) <= limit
                ):
                    raise ValueError(
                        f"Completion requires {name} in 1–{limit} characters. "
                        "Summarize the outcome and link the full deliverable saved with "
                        "save_document; do not include a work log or omit material uncertainty."
                    )
            if issue["id"] == session["issue"] and any(
                child["state"] != "done" for child in self.store.children(slug, session["id"])
            ):
                raise ValueError("Wait for delegated results before completing the parent task.")
            children = issue["children"]
            if children["pageInfo"]["hasNextPage"] or any(
                c["state"]["type"] not in {"completed", "canceled", "duplicate"}
                for c in children["nodes"]
            ):
                raise ValueError("Complete or resolve open sub-issues before closing their parent.")
            fields = {"stateId": first_state(workflow, "completed")["id"]}
        if action != "comment":
            self.validate_fields(agent, registry, workflow, fields)
            state = next(
                (s for s in workflow["states"]["nodes"] if s["id"] == fields.get("stateId")), None
            )
            if state and state["type"] == "completed" and action != "complete":
                raise ValueError("Use complete with the verified deliverable to mark a task Done.")
        if action == "create" and any(
            s["id"] == fields["stateId"] and s["type"] == "canceled"
            for s in workflow["states"]["nodes"]
        ):
            raise ValueError("Create actionable tasks, not canceled records.")
        if action == "start" and issue["state"]["type"] in {"completed", "canceled", "duplicate"}:
            raise ValueError("Do not automatically reopen a terminal issue.")
        body = (
            payload.get("body")
            if action == "comment"
            else (
                payload["result"].strip() + "\n\n**Проверено:** " + payload["verification"].strip()
                if action == "complete"
                else None
            )
        )
        if action in {"comment", "complete"} and (
            not isinstance(body, str) or not body.strip() or len(body) > 10000
        ):
            raise ValueError("A bounded non-empty comment is required.")
        request = {
            "action": action,
            "issue": issue["id"] if issue else None,
            "team": team,
            "fields": fields,
            "body": body,
        }
        operation = self.store.issue_operation(session, key, request)
        if operation["state"] == "done":
            return json.loads(operation["result"])
        identity = operation["id"]
        if action == "create":
            try:
                existing = self.linear.issue_details(agent, identity)
            except RemoteError:
                existing = None
            if not existing:
                self.linear.create_issue(agent, {"id": identity, "teamId": team, **fields})
                existing = self.linear.issue_details(agent, identity)
            if (
                not existing
                or existing["team"]["id"] != team
                or (existing.get("creator") or {}).get("id")
                != self.store.installation(slug)["app_user"]
            ):
                raise PermissionError("Created issue identity conflict.")
            issue = existing
        else:
            if body is not None:
                comment_id = stable_id(identity + ":comment")
                try:
                    existing = self.linear.comment(agent, comment_id)
                except RemoteError:
                    existing = None
                if existing and (
                    existing["body"] != body or existing["issue"]["id"] != issue["id"]
                ):
                    raise PermissionError("Comment identity conflict.")
                if not existing:
                    self.linear.create_comment(agent, comment_id, issue["id"], body)
            if action != "comment" and not matches(issue, fields):
                if not self.store.claim_issue_operation(identity):
                    raise RemoteError()  # Unknown prior write: never overwrite a later human edit.
                self.linear.update_issue(agent, issue["id"], fields)
                issue = self.linear.issue_details(agent, issue["id"])
                if not matches(issue, fields):
                    raise RemoteError()
        result = receipt(issue)
        self.store.finish_issue_operation(identity, result)
        return result

    @staticmethod
    def excerpt(text, offset, field):
        if type(offset) is not int or not 0 <= offset <= len(text):
            raise ValueError("Invalid content offset.")
        return {
            field: text[offset : offset + 12000],
            "next_offset": offset + 12000 if offset + 12000 < len(text) else None,
        }

    def materials(self, agent, registry, issue, payload):
        action = payload["action"]
        offset = payload.get("offset", 0)
        scope = ProjectScope(self, agent, registry, issue)
        if action == "comments":
            cursor = payload.get("cursor")
            if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 512):
                raise ValueError("Invalid comment cursor.")
            # One complete comment per page; long text is readable with offset.
            result = self.linear.issue_comments(agent, issue["id"], cursor)
            for row in result["nodes"]:
                row.update(self.excerpt(row.get("body") or "", offset, "body"))
            return result
        if action == "project":
            if not scope.project:
                return {
                    "project": None,
                    "shared_task_root": scope.root,
                    "message": "No Linear project is assigned; use the shared parent task tree.",
                }
            result = self.linear.project_materials(agent, scope.project)
        else:
            result = self.linear.document(agent, identifier(payload.get("document")))
            linked_issue = (result.get("issue") or {}).get("id")
            if linked_issue:
                scope.require(scope.get(linked_issue))
            elif not scope.project or (result.get("project") or {}).get("id") != scope.project:
                raise PermissionError("The document is outside this task's project.")
        result.update(self.excerpt(result.get("content") or "", offset, "content"))
        return result

    def save_document(self, agent, registry, session, issue, payload):
        fields = payload.get("fields")
        key = payload.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key):
            raise ValueError("Document writes require a stable request key.")
        if (
            not isinstance(fields, dict)
            or set(fields) != {"title", "content"}
            or any(
                not isinstance(fields[name], str)
                or not fields[name].strip()
                or len(fields[name]) > limit
                for name, limit in (("title", 200), ("content", 12000))
            )
        ):
            raise ValueError("Provide a title and bounded document content.")
        document = payload.get("document")
        if document:
            self.materials(agent, registry, issue, {"action": "document", "document": document})
        operation = self.store.issue_operation(
            session,
            key,
            {
                "action": "save_document",
                "issue": issue["id"],
                "document": document,
                "fields": fields,
            },
        )
        if operation["state"] == "done":
            return json.loads(operation["result"])
        identity = document or operation["id"]
        try:
            existing = self.linear.document(agent, identity)
        except RemoteError:
            existing = None
        if existing and any(existing.get(name) != value for name, value in fields.items()):
            if not document or not self.store.claim_issue_operation(operation["id"]):
                raise RemoteError()  # Never overwrite a later human edit on an uncertain retry.
            self.linear.save_document(agent, identity, fields)
        elif not existing:
            if document:
                raise ValueError("The requested document no longer exists.")
            self.linear.save_document(
                agent, identity, {**fields, "issueId": issue["id"]}, create=True
            )
        saved = self.linear.document(agent, identity)
        if not saved or any(saved.get(name) != value for name, value in fields.items()):
            raise RemoteError()
        if not document and (saved.get("issue") or {}).get("id") != issue["id"]:
            raise PermissionError("Created document identity conflict.")
        result = {name: saved.get(name) for name in ("id", "title", "url")}
        self.store.finish_issue_operation(operation["id"], result)
        return result

    def begin_delegated(self, agent, remote):
        issue = remote["issue"]
        installation = self.store.installation(agent.slug)
        if (issue.get("delegate") or {}).get("id") != installation["app_user"]:
            return  # A comment mention alone does not start the whole issue.
        current = self.linear.issue_details(agent, issue["id"])
        if (current.get("delegate") or {}).get("id") != installation["app_user"]:
            return
        if current["state"]["type"] in {"started", "completed", "canceled", "duplicate"}:
            return
        state = first_state(self.linear.workflow(agent, issue["team"]["id"]), "started")
        self.linear.update_issue(agent, issue["id"], {"stateId": state["id"]})
