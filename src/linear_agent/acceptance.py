"""Explicit coordinator acceptance, with retained decisions and owner reservations."""

import hashlib
import json
import re
from urllib.parse import urlsplit

from .store import stable_id
from .transport import RemoteError

CATEGORIES = (
    "internal-research",
    "internal-draft",
    "documentation",
    "sandbox-validation",
    "internal-planning",
)
OWNER_LABELS = {"acceptance:owner", "owner-review", "important", "важное", "приемка владельца"}
# Conservative signals, not a substitute for checking the actual scope and evidence.
# Once observed, they cannot be erased by an agent editing the task before accepting it.
OWNER_SIGNALS = {
    "publication or external communication": r"публикац|опубликов|рассылк|отправ.+от.+имен|для сайта|на сайте|для топ|руководств|\bpublish|\bpublic(?:ation)?\b|\boutreach\b|website copy|top management",
    "production or irreversible changes": r"\bprod(?:uction)?\b|\bdeploy|protected.branch|\bmerge\b|боев.+сред|продак|депло|необратим|удалени.+данн|\bdestructive\b|\birreversible\b",
    "security, access or sensitive data": r"\bsecurity\b|\bsecret|\bpermission|\bcredential|sensitive.data|безопасност|секрет|прав.+доступ|доступ.+данн|персональн.+данн",
    "financial, legal or strategic decisions": r"\bbudget\b|\bspend|\bpayment|\bcontract|\blegal\b|\bstrateg|бюджет|расход|платеж|оплат|договор|юридич|стратег|измен.+приоритет",
    "explicit owner acceptance": r"owner.{0,30}(?:approval|acceptance)|(?:прием|согласов|утвержд).{0,30}(?:владел|константин)|(?:принимаю|закрываю).{0,8}(?:сам|я)\b|не закрыва|не перевод.+done",
}


def revision(issue):
    """Bind acceptance to the reviewed brief, scope, labels and discussion, not status writes."""
    fields = {
        name: issue.get(name)
        for name in (
            "id",
            "title",
            "description",
            "priority",
            "project",
            "parent",
            "assignee",
            "labels",
            "children",
            "documents",
            "attachments",
            "comments",
        )
    }
    fields["comments"] = issue.get("comments", {}).get("nodes", [])[-50:]
    return hashlib.sha256(
        json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def policy(store, issue, owner):
    labels = {x.get("name", "").casefold() for x in issue.get("labels", {}).get("nodes", [])}
    comments = issue.get("comments", {}).get("nodes", [])
    text = (
        "\n".join(
            [issue.get("title") or "", issue.get("description") or ""]
            + [c.get("body") or "" for c in comments if (c.get("user") or {}).get("id") == owner]
        )
        .casefold()
        .replace("ё", "е")
    )
    reasons = [name for name, pattern in OWNER_SIGNALS.items() if re.search(pattern, text)]
    if issue.get("comments", {}).get("pageInfo", {}).get("hasPreviousPage"):
        reasons.append("older discussion may contain owner acceptance conditions")
    if labels & OWNER_LABELS:
        reasons.append("owner acceptance label")
    if issue.get("priority") in {1, 2}:
        reasons.append("urgent or high priority")
    reasons = store.owner_review_reasons(issue["id"], reasons)
    return {"revision": revision(issue), "owner_required": bool(reasons), "reasons": reasons}


class Acceptance:
    def __init__(self, issues):
        self.issues = issues
        self.store, self.linear = issues.store, issues.linear

    def execute(self, agent, registry, session, issue, payload, *, coordinator):
        if not coordinator:
            raise PermissionError("Only the root Qui-Gon coordinator may accept ordinary tasks.")
        if set(payload) - {
            "run",
            "session",
            "action",
            "issue",
            "key",
            "revision",
            "category",
            "result",
            "verification",
            "evidence",
            "artifact_version",
        }:
            raise ValueError("Acceptance cannot also edit the task or its risk classification.")
        for name, limit in (
            ("result", 500),
            ("verification", 160),
            ("artifact_version", 160),
            ("evidence", 2048),
        ):
            value = payload.get(name)
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
                raise ValueError(f"Acceptance requires {name} in 1–{limit} characters.")
        evidence = urlsplit(payload["evidence"])
        if (
            evidence.scheme != "https"
            or not evidence.hostname
            or evidence.username
            or evidence.password
        ):
            raise ValueError("Link the actual reviewed artifact using an HTTPS URL.")
        if payload.get("category") not in CATEGORIES:
            raise ValueError(
                "Only ordinary internal deliverables qualify for coordinator acceptance."
            )
        if not re.fullmatch(r"[a-f0-9]{64}", payload.get("revision", "")):
            raise ValueError("Read the issue and use its acceptance.revision before accepting it.")
        key = payload.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", key):
            raise ValueError("Acceptance requires a stable request key.")
        body = (
            "**Приемка Квай-Гона:** "
            + payload["result"].strip()
            + "\n\n**Проверено:** "
            + payload["verification"].strip()
            + "\n\n"
            + payload["evidence"]
            + "\nВерсия: "
            + payload["artifact_version"].strip()
            + "\nОбычная внутренняя задача; приемка владельца не требуется."
        )
        if len(body) > 700:
            raise ValueError("Keep the whole acceptance comment within 700 characters.")
        request = {
            name: payload[name]
            for name in (
                "revision",
                "category",
                "result",
                "verification",
                "evidence",
                "artifact_version",
            )
        }
        request.update(action="accept", issue=issue["id"], actor=agent.slug, owner=registry.owner)
        operation = self.store.issue_operation(session, key, request, retained=True)
        identity = operation["id"]
        comment_id = stable_id(identity + ":comment")
        workflow = self.linear.workflow(agent, issue["team"]["id"])
        from .issues import first_state, is_review, receipt

        done = first_state(workflow, "completed")

        def check(current):
            # Our own audit comment is not a new owner instruction or deliverable revision.
            current["comments"]["nodes"] = [
                c for c in current["comments"]["nodes"] if c["id"] != comment_id
            ]
            decision = policy(self.store, current, registry.owner)
            if decision["owner_required"]:
                raise PermissionError(
                    "Owner acceptance required: " + "; ".join(decision["reasons"])
                )
            if decision["revision"] != payload["revision"]:
                raise ValueError("The task changed after review. Read and verify it again.")
            if not is_review(current["state"]) and not (
                operation["state"] in {"attempted", "done"} and current["state"]["id"] == done["id"]
            ):
                raise ValueError("Only a verified task already In Review can be accepted.")
            children = current["children"]
            if children["pageInfo"]["hasNextPage"] or any(
                c["state"]["type"] not in {"completed", "canceled", "duplicate"}
                for c in children["nodes"]
            ):
                raise ValueError("Accept or resolve outstanding sub-issues before the parent.")
            if current["id"] == session["issue"] and any(
                child["state"] != "done" for child in self.store.children(agent.slug, session["id"])
            ):
                raise ValueError("Wait for delegated execution to finish before acceptance.")

        check(issue)
        if operation["state"] == "done":
            if issue["state"]["id"] != done["id"]:
                raise ValueError(
                    "The accepted task was reopened. Do not repeat the old acceptance."
                )
            return json.loads(operation["result"])
        try:
            existing = self.linear.comment(agent, comment_id)
        except RemoteError:
            existing = None
        if existing and (existing["body"] != body or existing["issue"]["id"] != issue["id"]):
            raise PermissionError("Acceptance comment identity conflict.")
        if not existing:
            self.linear.create_comment(agent, comment_id, issue["id"], body)
        current = self.issues.checked_issue(agent, registry, issue["id"])
        check(current)
        if current["state"]["id"] != done["id"]:
            if not self.store.claim_issue_operation(identity):
                raise RemoteError()  # Never repeat an uncertain write over a later human edit.
            operation["state"] = "attempted"
            self.linear.update_issue(agent, issue["id"], {"stateId": done["id"]})
            current = self.issues.checked_issue(agent, registry, issue["id"])
            if current["state"]["id"] != done["id"]:
                raise RemoteError()
            check(current)
        result = {**receipt(current), "acceptance": request, "decision_id": identity}
        self.store.finish_issue_operation(identity, result)
        return result
