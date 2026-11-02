"""Explain execution and project interruptions into Linear without accepting results."""

import hashlib
import json
import logging

from .catalog import ROLES
from .handoff import Handoffs
from .transport import RemoteError
from .workflow import TERMINAL, first_state, is_blocked, is_review, named_state

logger = logging.getLogger(__name__)

PHASES = {
    "queued": ("В очереди", "Исполнитель начнет работу."),
    "starting": ("Запускается", "Ожидается подтверждение запуска."),
    "running": ("В работе", "Исполнитель готовит результат."),
    "waiting": ("Нужен ответ", "Ответьте на вопрос исполнителя в задаче."),
    "waiting_children": ("Ждет подзадачи", "Координатор продолжит после исполнителей."),
    "publishing": ("Передает результат", "Ожидается подтверждение сохранения ответа."),
    "done": ("Выполнение завершено", "Проверить результат и статус приемки задачи."),
    "failed": (
        "Нужна помощь",
        "Устранить причину остановки и продолжить с сохраненного результата.",
    ),
    "cancelled": ("Выполнение остановлено", "Решить, продолжать работу или отменить задачу."),
    "cancelling": ("Останавливается", "Ожидается подтверждение остановки."),
    "retrying": ("Ждет восстановления связи", "Система повторит запрос автоматически."),
    "blocked": ("Нужна помощь координатора", "Координатор должен устранить причину блокировки."),
    "blocked_children": (
        "Подзадаче нужна помощь",
        "Координатор должен устранить блокировку подзадачи.",
    ),
}
BLOCKING = {
    "waiting",
    "failed",
    "cancelled",
    "cancelling",
    "retrying",
    "blocked",
    "blocked_children",
}


class TaskStatus:
    def __init__(self, service):
        self.service, self.store = service, service.store

    def execution(self, job, jobs=None):
        if not job:
            return None
        phase = job["state"]
        retry = self.service.recovery.get("job:" + job["key"])
        if retry["failures"] and phase not in {"done", "failed", "cancelled", "cancelling"}:
            phase = "retrying"
        if phase == "publishing" and job["error"]:
            phase = "failed"
        handoff = Handoffs(self.service).for_job(job["key"])
        if phase in {"done", "publishing"} and handoff and handoff["payload"]["kind"] == "blocked":
            phase = "blocked"
        children = []
        if job["agent"] == "qui-gon":
            jobs = self.store.task_jobs() if jobs is None else jobs
            for child in self.store.children(job["agent"], job["session"]):
                value = self.execution(jobs.get(child["issue"]), {})
                children.append({"issue": child["issue"], "execution": value})
            active = any(
                c["execution"] and c["execution"]["state"] not in {"done", "failed", "cancelled"}
                for c in children
            )
            if phase == "done" and active:
                phase = "waiting_children"
            if phase == "waiting_children" and any(
                c["execution"] and c["execution"]["state"] in BLOCKING for c in children
            ):
                phase = "blocked_children"
        label, next_action = PHASES[phase]
        if phase == "blocked" and handoff:
            next_action = handoff["payload"]["next_action"]
        return {
            "state": phase,
            "label": label,
            "responsible": {"agent": job["agent"], "name": ROLES[job["agent"]]["name"]},
            "waiting_on": "owner"
            if phase == "waiting"
            else "coordinator"
            if phase in {"blocked", "blocked_children", "failed", "cancelled"}
            else None,
            "next_action": next_action,
            "reason": handoff["payload"]["summary"] if phase == "blocked" and handoff else None,
            "retry_at": retry["next_at"] if phase == "retrying" else None,
            "children": children,
            "has_active_children": any(
                c["execution"] and c["execution"]["state"] not in {"done", "failed", "cancelled"}
                for c in children
            ),
        }

    def describe(self, issue):
        if not issue:
            raise RemoteError()
        job = self.store.task_jobs().get(issue["id"])
        return {
            "workflow": issue["state"],
            "responsible": issue.get("delegate") or issue.get("assignee"),
            "human_assignee": issue.get("assignee"),
            "execution": self.execution(job),
            "acceptance": "accepted"
            if issue["state"]["type"] == "completed"
            else "awaiting_review"
            if is_review(issue["state"])
            else "not_submitted",
        }

    def reconcile(self, registry):
        if not registry.enabled or (self.service.home / "runtime/maintenance").exists():
            return
        jobs = self.store.task_jobs()
        for issue_id, job in jobs.items():
            if job["owner"] != registry.owner or job["revision"] != registry.revision:
                continue
            execution = self.execution(job, jobs)
            fingerprint = hashlib.sha256(
                json.dumps(
                    [job["key"], execution["state"], job["error"], execution["has_active_children"]]
                ).encode()
            ).hexdigest()
            with self.store.database() as db:
                row = db.execute(
                    "SELECT * FROM task_projections WHERE issue=?", (issue_id,)
                ).fetchone()
            if row and row["fingerprint"] == fingerprint:
                continue  # Event-driven projection, not polling every historical issue.
            key = "status:" + issue_id
            if not self.service.recovery.due(key):
                continue
            agent = registry.agents[job["agent"]]
            try:
                issue = self.service.linear.task_status(agent, issue_id)
                installation = self.store.installation(agent.slug)
                if not issue or issue["team"]["id"] not in registry.teams or not installation:
                    raise PermissionError("Task scope changed.")
                managed = bool(
                    row and row["blocked_by_adapter"] and row["state_id"] == issue["state"]["id"]
                )
                state = issue["state"]
                owned = (issue.get("delegate") or {}).get("id") == installation["app_user"]
                if (
                    owned
                    and state["type"] not in TERMINAL
                    and (not is_review(state) or execution["has_active_children"])
                ):
                    blocked = execution["state"] in BLOCKING or execution["state"] == "done"
                    target = None
                    if blocked and state["type"] == "started" and not is_blocked(state):
                        workflow = self.service.linear.workflow(agent, issue["team"]["id"])
                        target = named_state(workflow, is_blocked, "Blocked")
                        managed = True
                    elif (
                        (managed or is_review(state))
                        and not blocked
                        and execution["state"] in {"running", "waiting_children", "publishing"}
                    ):
                        target = first_state(
                            self.service.linear.workflow(agent, issue["team"]["id"]), "started"
                        )
                        managed = False
                    if target:
                        # Remember ownership before an uncertain write, then reconcile by readback.
                        with self.store.database() as db:
                            db.execute(
                                "INSERT INTO task_projections VALUES(?,'',?,?,?) ON CONFLICT(issue) DO UPDATE SET fingerprint='',blocked_by_adapter=excluded.blocked_by_adapter,state_id=excluded.state_id,updated=excluded.updated",
                                (
                                    issue_id,
                                    1,
                                    target["id"] if managed else state["id"],
                                    self.service.clock(),
                                ),
                            )
                        self.service.linear.update_issue(agent, issue_id, {"stateId": target["id"]})
                        issue = self.service.linear.task_status(agent, issue_id)
                        if not issue or issue["state"]["id"] != target["id"]:
                            raise RemoteError()
                with self.store.database() as db:
                    db.execute(
                        "INSERT INTO task_projections VALUES(?,?,?,?,?) ON CONFLICT(issue) DO UPDATE SET fingerprint=excluded.fingerprint,blocked_by_adapter=excluded.blocked_by_adapter,state_id=excluded.state_id,updated=excluded.updated",
                        (
                            issue_id,
                            fingerprint,
                            int(managed),
                            issue["state"]["id"],
                            self.service.clock(),
                        ),
                    )
                self.service.recovery.success(key)
            except (RemoteError, ValueError, PermissionError) as error:
                self.service.recovery.failure(
                    key, error if isinstance(error, RemoteError) else RemoteError()
                )
                logger.warning("Task status reconciliation deferred; execution state retained.")
