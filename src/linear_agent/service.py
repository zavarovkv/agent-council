"""Verified owner requests, resumable runs, native clarification and sub-issue routing."""

import json
import logging
import re
import threading
import time
from pathlib import Path

from .config import Registry, identifier
from .events import parse_event, verified_payload
from .hermes import Hermes
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

    def registry(self):
        return Registry.load(self.home)

    def ingest(self, slug, body, signature):
        if (self.home / "runtime/maintenance").exists():
            raise BlockingIOError("Maintenance admission is paused.")
        registry = self.registry()
        agent = registry.agents[slug]
        payload = verified_payload(body, signature, agent.webhook_secret, self.clock())
        if not registry.enabled:
            raise BlockingIOError("Linear execution is disabled.")
        event = parse_event(payload, registry, agent)
        if event is None:
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
            if event.action != "created" or child is None:
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
        if not child and owner == registry.owner and (issue.get("parent") or {}).get("id"):
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
                or (issue.get("parent") or {}).get("id") != parent["issue"]
                or (issue.get("delegate") or {}).get("id") != installation["app_user"]
                or (issue.get("creator") or {}).get("id")
                not in {origin["app_user"], registry.owner}
            ):
                raise PermissionError("Sub-issue lineage is not authorized.")
            self.authorize(registry, registry.agents[parent["agent"]], parent["id"])
        elif owner != registry.owner:
            raise PermissionError("Only the configured owner may initiate agent work.")
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
        self.store.update_job(job["key"], state="cancelled")

    def step(self):
        registry = self.registry()
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
        if job["action"] == "prompted" and waiting:
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
                self.store.child_finished(agent.slug, job["session"], job["output"])
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
            else "Продолжаю работу с вашим уточнением.",
        )
        self.flush_outbox(registry)
        if not self.store.published(ack):
            return
        if self.ready_until.get(agent.slug, 0) <= self.clock():
            self.hermes.ready(agent)
            self.ready_until[agent.slug] = self.clock() + 30
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
            current = {**control, "execution_generation": result["execution_generation"]}
            self.store.publish(
                agent.slug,
                job["session"],
                job["key"] + ":clarify:" + control["prompt_id"],
                "elicitation",
                question,
            )
            self.store.update_job(job["key"], state="waiting", control=json.dumps(current))
            return
        if job["state"] == "waiting" and result.get("status") in {"running", "queued"}:
            self.store.update_job(job["key"], state="running", control="")
        if result.get("status") == "completed":
            output = result.get("output")
            if not isinstance(output, str) or not output.strip():
                raise ValueError("Executor returned no final result.")
            children = self.store.children(agent.slug, job["session"])
            if any(
                child["state"] != "done" or child["origin_job"] == job["key"] for child in children
            ):
                self.store.publish(
                    agent.slug,
                    job["session"],
                    job["key"] + ":children",
                    "thought",
                    "Подзадачи переданы исполнителям. Соберу результаты после завершения.",
                )
                self.store.update_job(job["key"], state="waiting_children", output=output[:16000])
                return
            self.store.publish(
                agent.slug, job["session"], job["key"] + ":final", "response", output[:16000]
            )
            self.store.update_job(job["key"], state="publishing", output=output[:16000])
        elif self.clock() - (job["received"] if job["state"] == "waiting" else job["updated"]) > (
            86400 if job["state"] == "waiting" else 1800
        ):
            self._cancel(agent, job)
            self._fail(agent, job, "execution_timeout")

    def _fail(self, agent, job, category):
        message = "Не удалось завершить работу. Состояние сохранено; автоматический повтор выполнения отключён."
        if category == "clarification_uncertain":
            message = "Не удалось подтвердить применение вашего уточнения. Работа остановлена; ответ не отправлен повторно. Проверьте результат и создайте новую сессию."
        self.store.publish(agent.slug, job["session"], job["key"] + ":final", "error", message)
        self.store.update_job(job["key"], state="publishing", error=category, output=message)

    def delegate(self, slug, payload):
        registry = self.registry()
        agent = registry.agents[slug]
        if (
            set(payload) != {"run", "session", "target", "key", "title", "description"}
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
        )
        issue = self.linear.ensure_child(
            agent,
            child,
            parent_issue=session["issue"],
            team=remote["issue"]["team"]["id"],
            delegate=installation["app_user"],
        )
        return {"issue_id": issue["id"], "url": issue["url"], "agent": target.slug}

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
            }
            for child in self.store.children(slug, session["id"])
        ]

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
                and all(item["state"] == "done" for item in children)
                and not any(job["state"] != "waiting_children" for job in active)
            ):
                try:
                    self.authorize(registry, registry.agents[pair[0]], pair[1])
                    self.store.notify_parent(
                        parent,
                        [item for item in children if not item["notified"]],
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
