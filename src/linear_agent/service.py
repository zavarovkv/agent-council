"""Verified owner requests, resumable runs, native clarification and sub-issue routing."""

import json
import logging
import re
import threading
import time
from pathlib import Path

from .catalog import LIMITATIONS, ROLES
from .config import Registry, identifier
from .events import parse_event, verified_payload
from .handoff import LEGACY_LIMIT, Handoffs
from .hermes import Hermes
from .issues import Issues
from .linear import Linear
from .store import Store, stable_id
from .transport import RemoteError

logger = logging.getLogger(__name__)
FINAL = {"done", "failed", "cancelled"}


class Service:
    def __init__(self, home, *, store=None, linear=None, hermes=None, clock=time.time):
        self.home, self.clock = Path(home), clock
        self.store = store or Store(home, clock=clock)
        self.linear = linear or Linear(self.store, clock=clock)
        self.hermes = hermes or Hermes()
        self.halt = threading.Event()
        self.wake = threading.Event()
        self.ready_until = {}
        from .intake import Intake
        from .recurring import Recurring

        self.recurring = Recurring(self)
        self.intake = Intake(self)

    def root_admission(self, registry, agent, issue, sid=None):
        for source in (self.recurring, self.intake):
            row = source.authorized(registry, agent, issue, sid)
            if row:
                return source, row
        return None, None

    def registry(self):
        return Registry.load(self.home)

    def ingest(self, slug, body, signature):
        if (self.home / "runtime/maintenance").exists():
            raise BlockingIOError("Maintenance admission is paused.")
        registry = self.registry()
        agent = registry.agents[slug]
        payload = verified_payload(body, signature, agent.webhook_secret, self.clock())
        event = parse_event(payload, registry, agent)
        if event is None:
            return False
        if not registry.enabled:
            raise BlockingIOError("Linear execution is disabled.")
        if event.created < registry.admission_since:
            return False
        installation = self.store.installation(slug)
        if (
            not installation
            or installation["organization"] != registry.organization
            or payload["appUserId"] != installation["app_user"]
        ):
            raise PermissionError("Unrecognized app installation.")
        if event.actor != registry.owner:
            child = self.store.delegation(slug, payload["agentSession"].get("issueId", ""))
            _, scheduled = self.root_admission(
                registry, agent, payload["agentSession"].get("issueId", ""), event.session
            )
            if event.action != "created" or (
                child is None and not (scheduled and event.actor in {"", installation["app_user"]})
            ):
                return False
        result = self.store.admit(event, registry.revision)
        self.wake.set()
        return result

    def authorize(self, registry, agent, sid, job=None):
        if not registry.enabled:
            raise PermissionError("Linear execution is disabled.")
        installation = self.store.installation(agent.slug)
        if not installation or installation["organization"] != registry.organization:
            raise PermissionError("Installation scope changed.")
        remote = self.linear.session(agent, sid)
        issue = remote.get("issue") or {}
        if (
            remote.get("id") != sid
            or remote.get("appUser", {}).get("id") != installation["app_user"]
            or remote.get("dismissedAt")
            or remote.get("archivedAt")
            or remote.get("status") == "stopping"
            or issue.get("team", {}).get("id") not in registry.teams
        ):
            raise PermissionError("The app no longer owns an allowed issue session.")
        issue_id = identifier(issue.get("id"))
        owner = (remote.get("creator") or {}).get("id")
        child = self.store.delegation(agent.slug, issue_id)
        if (
            not child
            and owner == registry.owner
            and (issue.get("parent") or {}).get("id")
            and any(agent.slug in candidate.delegates for candidate in registry.agents.values())
        ):
            parent = self.store.parent_session(issue["parent"]["id"])
            coordinator = registry.agents[parent["agent"]]
            if (
                agent.slug not in coordinator.delegates
                or (issue.get("creator") or {}).get("id") != registry.owner
            ):
                raise PermissionError("This sub-issue has no authorized coordinator.")
            self.authorize(registry, coordinator, parent["id"])
            child = self.store.reserve_child(
                parent,
                agent.slug,
                "manual:" + issue_id,
                issue.get("title") or "Sub-issue",
                issue.get("description") or "",
                issue=issue_id,
            )
        if child:
            parent = self.store.session(child["parent_agent"], child["parent_session"])
            origin = self.store.installation(child["parent_agent"])
            if (
                not parent
                or parent["blocked"]
                or parent["owner"] != registry.owner
                or parent["depth"]
                or not origin
                or (issue.get("parent") or {}).get("id")
                != (child["parent_issue"] or parent["issue"])
                or (issue.get("delegate") or {}).get("id") != installation["app_user"]
                or (issue.get("creator") or {}).get("id")
                not in {origin["app_user"], registry.owner}
            ):
                raise PermissionError("Sub-issue lineage is not authorized.")
            self.authorize(registry, registry.agents[parent["agent"]], parent["id"])
        elif owner != registry.owner:
            source, scheduled = self.root_admission(registry, agent, issue_id, sid)
            if (
                not scheduled
                # Linear leaves creator null for automatically delegated sessions.
                # Only a pinned owner admission and app-owned issue grant this exception.
                or owner not in {None, installation["app_user"]}
                or (issue.get("creator") or {}).get("id") != installation["app_user"]
                or (issue.get("delegate") or {}).get("id") != installation["app_user"]
                or issue["team"]["id"] != json.loads(scheduled["body"])["team"]
            ):
                raise PermissionError(
                    "Only the owner or a registered owner request may initiate work."
                )
            source.bind(scheduled["key"], sid)
        if job and job["action"] == "prompted":
            activity = self.linear.activity(agent, job["activity"])
            if (
                activity.get("agentSession", {}).get("id") != sid
                or activity.get("user", {}).get("id") != registry.owner
                or job["actor"] != registry.owner
                or activity.get("content", {}).get("type") != "prompt"
                or (activity["content"].get("body") or "") != job["text"]
                or (activity.get("signal") == "stop") != bool(job["stop"])
            ):
                raise PermissionError("Only a verified owner continuation is accepted.")
        bound = self.store.bind(agent.slug, sid, issue_id, registry.owner, parent=child)
        return bound, remote

    def flush_outbox(self, registry):
        for record in self.store.outbound():
            agent = registry.agents.get(record["agent"])
            if not agent:
                continue
            try:
                self.authorize(registry, agent, record["session"])
                self.linear.ensure_activity(agent, record)
                self.store.publication(record["id"], confirmed=True)
            except PermissionError:
                self.store.cancel(agent.slug, record["session"])
            except RemoteError:
                self.store.publication(record["id"], confirmed=False)

    def _cancel(self, agent, job):
        if job["run_id"]:
            try:
                self.hermes.stop(agent, job["run_id"])
            except RemoteError as error:
                # Stop is idempotent and is retried by the next sweep. Never
                # start another model turn merely to recover a stop receipt.
                if error.status != 404:
                    self.store.update_job(job["key"], state="cancelling")
                    return
        self.store.update_job(
            job["key"],
            state="cancelled",
            output="Подзадача остановлена; подтвержденного результата нет.",
        )
        self.store.child_finished(agent.slug, job["session"], job["key"])

    def step(self):
        registry = self.registry()
        self.recurring.reconcile(registry)
        self.intake.reconcile(registry)
        jobs = self.store.jobs()
        for saved in jobs:
            job = self.store.job(saved["key"])
            if job["state"] in FINAL:
                continue
            agent = registry.agents.get(job["agent"])
            if not agent:
                self.store.update_job(job["key"], state="cancelled")
                continue
            if (
                not registry.enabled
                or job["revision"] != registry.revision
                or job["state"] == "cancelling"
            ):
                self.store.cancel(agent.slug, job["session"])
                self._cancel(agent, job)
                continue
            try:
                self._job(registry, agent, job, self.store.jobs())
            except PermissionError:
                self.store.cancel(agent.slug, job["session"])
                self._cancel(agent, job)
            except RemoteError:
                # Accepted run IDs and outputs persist. A temporary outage is
                # retried without repeating provider spend or losing the turn.
                age = self.clock() - (
                    job["received"] if job["state"] in {"starting", "waiting"} else job["updated"]
                )
                if age > (86400 if job["state"] == "waiting" else 1800):
                    self._cancel(agent, job)
                logger.warning(
                    "Linear integration temporarily unavailable; private details omitted."
                )
            except (KeyError, ValueError, TypeError):
                self._fail(agent, job, "protocol_error")
        self.flush_outbox(registry)
        self._notify_children(registry)

    def _job(self, registry, agent, job, jobs):
        session, remote = self.authorize(registry, agent, job["session"], job)
        if job["stop"]:
            for current in jobs:
                if (
                    current["agent"] == agent.slug
                    and current["session"] == job["session"]
                    and current["key"] != job["key"]
                ):
                    self._cancel(agent, current)
            self.store.cancel(agent.slug, job["session"])
            self.store.update_job(job["key"], state="done")
            return
        prior = [
            item
            for item in jobs
            if item["agent"] == agent.slug
            and item["session"] == job["session"]
            and item["sequence"] < job["sequence"]
            and item["state"] not in FINAL
        ]
        waiting = next((item for item in prior if item["state"] == "waiting"), None)
        if job["action"] in {"prompted", "native_reply"} and waiting:
            control = json.loads(waiting["control"])
            try:
                self.hermes.clarify(agent, waiting["run_id"], control, job["text"])
            except RemoteError as error:
                if error.status == 409:
                    current = self.hermes.run(agent, waiting["run_id"])
                    if any(
                        item.get("prompt_id") == control["prompt_id"]
                        for item in current.get("pending_controls", [])
                    ):
                        raise
                    # The native API has no caller-supplied control receipt.
                    # A settled prompt may have timed out, so never report the
                    # user's answer as applied after an ambiguous response.
                    self._cancel(agent, waiting)
                    self._fail(agent, waiting, "clarification_uncertain")
                    self.store.update_job(
                        job["key"], state="failed", error="clarification_uncertain"
                    )
                    return
                else:
                    raise
            self.store.update_job(waiting["key"], state="running", control="")
            self.store.update_job(job["key"], state="done")
            return
        if prior:
            return
        if job["state"] == "waiting_children":
            return
        if (
            job["action"] == "created"
            and not job["run_id"]
            and self.store.started(agent.slug, job["session"], job["key"])
        ):
            self.store.update_job(job["key"], state="cancelled")
            return
        if job["state"] == "publishing":
            output_id = stable_id(job["key"] + ":final")
            if self.store.published(output_id):
                self.store.update_job(job["key"], state="failed" if job["error"] else "done")
                self.store.child_finished(agent.slug, job["session"], job["key"])
            return
        if job["state"] in {"running", "waiting"}:
            self._poll(registry, agent, session, job)
            return
        if self.clock() - job["received"] > 86400:
            self.store.update_job(job["key"], state="cancelled")
            return
        ack = self.store.publish(
            agent.slug,
            job["session"],
            job["key"] + ":ack",
            "thought",
            "Начинаю работу над задачей."
            if job["action"] == "created"
            else "Продолжаю выполнение регулярного задания."
            if job["action"] == "scheduled_followup"
            else "Продолжаю работу после сообщения координатора."
            if job["action"] == "coordinator_reply"
            else "Проверяю результаты исполнителей."
            if job["action"] == "children"
            else "Продолжаю работу с вашим уточнением.",
        )
        self.flush_outbox(registry)
        if not self.store.published(ack):
            return
        if self.ready_until.get(agent.slug, 0) <= self.clock():
            self.hermes.ready(agent)
            self.ready_until[agent.slug] = self.clock() + 30
        Issues(self).begin_delegated(agent, remote)
        text = job["text"]
        if job["action"] == "created" and not text.strip():
            text = json.dumps(
                {"issue": remote["issue"], "directive": remote.get("comment")}, ensure_ascii=False
            )
        elif job["action"] == "prompted" and not self.store.started(
            agent.slug, job["session"], job["key"]
        ):
            text = json.dumps(
                {
                    "issue": remote["issue"],
                    "initial_directive": remote.get("comment"),
                    "owner_prompt": text,
                },
                ensure_ascii=False,
            )
        recurring_context = self.recurring.execution_context(
            registry, agent, remote["issue"]["id"], job["session"]
        )
        if recurring_context:
            text = json.dumps({"request": text, "recurring": recurring_context}, ensure_ascii=False)
        self.store.child_started(agent.slug, job["session"])
        self.store.update_job(job["key"], state="starting")
        result = self.hermes.start(agent, session["hermes"], text, job["key"])
        run_id = self.hermes.run_id(result.get("run_id"))
        self.store.update_job(job["key"], state="running", run_id=run_id)

    def _poll(self, registry, agent, session, job):
        result = self.hermes.run(agent, job["run_id"])
        if result.get("session_id") is not None and not isinstance(result["session_id"], str):
            raise ValueError("Invalid executor session.")
        if result.get("status") in {"unknown", "interrupted", "failed", "cancelled"}:
            self._fail(
                agent,
                job,
                "execution_unknown" if result["status"] == "unknown" else "execution_failed",
                checkpoint=result.get("output") if result["status"] == "failed" else None,
            )
            return
        controls = result.get("pending_controls") or []
        for control in controls:
            if control.get("kind") != "clarify":
                self._cancel(agent, job)
                self._fail(agent, job, "unsupported_approval")
                return
            question = control.get("question") or control.get("prompt") or control.get("body")
            if not isinstance(question, str) or not question.strip() or len(question) > 12000:
                raise ValueError("Invalid clarification.")
            question = re.sub(
                r"(?m)^Reply [\"']skip[\"'] to skip this question\.\s*$", "", question
            ).strip()
            current = {**control, "execution_generation": result["execution_generation"]}
            identity = job["key"] + ":clarify:" + control["prompt_id"]
            if not self.store.publication_exists(stable_id(identity)):
                mention = self.linear.owner_mention(agent, registry)
                self.store.publish(
                    agent.slug,
                    job["session"],
                    identity,
                    "elicitation",
                    f"{mention}\n\n{question}\n\nОтветьте в этой ветке.",
                )
            self.store.update_job(job["key"], state="waiting", control=json.dumps(current))
            return
        if job["state"] == "waiting" and result.get("status") in {"running", "queued"}:
            self.store.update_job(job["key"], state="running", control="")
        if result.get("status") == "completed":
            output = result.get("output")
            if not isinstance(output, str) or not output.strip():
                raise ValueError("Executor returned no final result.")
            if len(output) > LEGACY_LIMIT:
                self._fail(agent, job, "result_too_large")
                return
            children = self.store.children(agent.slug, job["session"])
            if any(
                child["state"] in {"reserved", "active"}
                or (
                    not child["notified"]
                    and (child["state"] == "blocked" or child["origin_job"] == job["key"])
                )
                for child in children
            ):
                self.store.publish(
                    agent.slug,
                    job["session"],
                    job["key"] + ":children",
                    "thought",
                    "Жду результаты подзадач от исполнителей.",
                )
                self.store.update_job(job["key"], state="waiting_children", output=output)
                return
            handoffs = Handoffs(self)
            try:
                event = handoffs.legacy(session, job, output)
            except ValueError:
                self._fail(agent, job, "handoff_storage_rejected", checkpoint=output)
                return
            # Review already posted the verified result in this run. The internal
            # handoff still reaches the coordinator, without another public copy.
            if event["payload"]["kind"] == "result" and self.store.reviewed(
                job["key"], session["issue"]
            ):
                self.store.update_job(job["key"], state="done", output=output)
                self.store.child_finished(agent.slug, job["session"], job["key"])
                return
            self.store.publish(
                agent.slug,
                job["session"],
                job["key"] + ":final",
                "response",
                event["payload"]["display"],
            )
            self.store.update_job(job["key"], state="publishing", output=output)
        elif self.clock() - (job["received"] if job["state"] == "waiting" else job["updated"]) > (
            86400 if job["state"] == "waiting" else 1800
        ):
            self._cancel(agent, job)
            self._fail(agent, job, "execution_timeout")

    def _fail(self, agent, job, category, *, checkpoint=None):
        message = "Не удалось завершить работу. Состояние сохранено; автоматический повтор выполнения отключён."
        if category == "result_too_large":
            message = "Результат превышает лимит внутреннего хранилища; он не принят и не обрезан. Сохраните полный материал в документ и передайте ссылку."
        if category == "clarification_uncertain":
            message = "Не удалось подтвердить применение вашего уточнения. Работа остановлена; ответ не отправлен повторно. Проверьте результат и создайте новую сессию."
        # Keep partial work available to the authorized coordinator without posting
        # a tool transcript or treating a failed run as completed research.
        prefix = "Исполнение не завершено. Частичный результат для продолжения:\n\n"
        output = message
        if isinstance(checkpoint, str) and len(prefix) + len(checkpoint) > LEGACY_LIMIT:
            message += " Частичный результат превышает лимит хранилища и не сохранён; обрезанная копия не принята."
            output = message
        if (
            isinstance(checkpoint, str)
            and checkpoint.strip()
            and len(prefix) + len(checkpoint) <= LEGACY_LIMIT
        ):
            output = prefix + checkpoint
        session = self.store.session(agent.slug, job["session"])
        if session:
            try:
                Handoffs(self).legacy(session, job, output, failed=True)
            except ValueError:
                # Quota failure must not recursively prevent terminalization.
                # The bounded executor output remains in the job checkpoint.
                message += " Внутреннее хранилище результатов недоступно; передача полного результата не подтверждена."
                if not checkpoint:
                    output = message
        self.store.publish(agent.slug, job["session"], job["key"] + ":final", "error", message)
        self.store.update_job(job["key"], state="publishing", error=category, output=output)

    def delegate(self, slug, payload):
        registry = self.registry()
        agent = registry.agents[slug]
        if (
            not {"run", "session", "target", "key", "title", "description"} <= set(payload)
            or not set(payload)
            <= {"run", "session", "target", "key", "title", "description", "parent_issue"}
            or payload["target"] not in agent.delegates
        ):
            raise PermissionError("Unregistered executor or tool parameters.")
        if (
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", payload["key"])
            or not isinstance(payload["title"], str)
            or not 1 <= len(payload["title"].strip()) <= 200
            or not isinstance(payload["description"], str)
            or not 1 <= len(payload["description"].strip()) <= 12000
        ):
            raise ValueError("Invalid bounded sub-issue request.")
        session = self.store.context(slug, payload["run"], payload["session"])
        current, remote = self.authorize(registry, agent, session["id"])
        if current["depth"]:
            raise PermissionError("Specialists cannot delegate further.")
        parent_issue = Issues(self).checked_issue(
            agent, registry, payload.get("parent_issue") or session["issue"]
        )
        if parent_issue["state"]["type"] in {"completed", "canceled", "duplicate"}:
            raise ValueError(
                "Use an active follow-up task as parent_issue; do not reopen accepted work."
            )
        target = registry.agents[payload["target"]]
        installation = self.store.installation(target.slug)
        if not installation or installation["organization"] != registry.organization:
            raise PermissionError("Install the specialist's Linear application first.")
        child = self.store.reserve_child(
            session,
            target.slug,
            payload["key"],
            payload["title"].strip(),
            payload["description"].strip(),
            parent_issue=parent_issue["id"],
        )
        issue = self.linear.ensure_child(
            agent,
            child,
            parent_issue=parent_issue["id"],
            team=parent_issue["team"]["id"],
            delegate=installation["app_user"],
        )
        return {"issue_id": issue["id"], "url": issue["url"], "agent": target.slug}

    def handoff(self, slug, payload):
        return Handoffs(self).execute(slug, payload)

    def issue_tasks(self, slug, payload):
        return Issues(self).execute(slug, payload)

    def child_tasks(self, slug, payload):
        if set(payload) != {"run", "session"}:
            raise ValueError("Invalid task context.")
        registry = self.registry()
        agent = registry.agents[slug]
        session = self.store.context(slug, payload["run"], payload["session"])
        self.authorize(registry, agent, session["id"])
        return [
            {
                "issue_id": child["issue"],
                "agent": child["child_agent"],
                "state": child["state"],
                "result": child["result"],
                "event_id": self.store.handoff_id(child["result_key"]),
            }
            for child in self.store.children(slug, session["id"])
        ]

    def agent_catalog(self, slug, payload):
        if set(payload) != {"run", "session"}:
            raise ValueError("Invalid task context.")
        registry = self.registry()
        agent = registry.agents[slug]
        session = self.store.context(slug, payload["run"], payload["session"])
        self.authorize(registry, agent, session["id"])
        records = []
        for target, role in ROLES.items():
            installed = self.store.installation(target)
            connected = bool(
                target in registry.agents
                and installed
                and installed["organization"] == registry.organization
            )
            records.append(
                {
                    "slug": target,
                    "name": role["name"],
                    "specialty": role["specialty"],
                    "deliverables": role["deliverables"],
                    "registered": target in registry.agents,
                    "installed": connected,
                    "delegation_allowed": connected and target in agent.delegates,
                    "model_readiness": "checked_at_run_start",
                }
            )
        return {"agents": records, "limitations": LIMITATIONS}

    def _notify_children(self, registry):
        seen = set()
        for child in self.store.ready_children():
            pair = child["parent_agent"], child["parent_session"]
            if pair in seen:
                continue
            seen.add(pair)
            parent = self.store.session(*pair)
            children = self.store.children(*pair)
            active = [job for job in self.store.jobs() if (job["agent"], job["session"]) == pair]
            if (
                parent
                and children
                and (
                    all(item["state"] in {"done", "blocked"} for item in children)
                    or any(item["state"] == "blocked" and not item["notified"] for item in children)
                )
                and not any(job["state"] != "waiting_children" for job in active)
            ):
                try:
                    self.authorize(registry, registry.agents[pair[0]], pair[1])
                    self.store.notify_parent(
                        parent,
                        [
                            item
                            for item in children
                            if not item["notified"] and item["state"] in {"done", "blocked"}
                        ],
                        registry.revision,
                    )
                except PermissionError:
                    for item in children:
                        self.store.child_notified(item["id"])

    def loop(self):
        while not self.halt.is_set():
            try:
                self.step()
            except Exception:
                if (self.home / "agents.json").exists():
                    logger.warning("Linear adapter is not ready; private details omitted.")
            self.wake.wait(2)
            self.wake.clear()
