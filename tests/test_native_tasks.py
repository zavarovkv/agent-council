import concurrent.futures
import copy
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_linear_agent import NOW, FakeHermes, configuration, session, uid
from test_recurring import RecurringLinear

from council_recurring.client import atomic_json
from linear_agent.http import Server
from linear_agent.service import Service
from linear_agent.store import Store
from linear_agent.transport import RemoteError


class NativeLinear(RecurringLinear):
    def create_issue(self, agent, fields):
        self.created_as, self.created_fields = agent.slug, copy.deepcopy(fields)
        super().create_issue(agent, fields)

    def task_status(self, agent, issue):
        return self.issue_details(agent, issue)


class NativeTasksTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.clock = NOW
        configuration(self.home)
        self.grant = {
            "schema": 1,
            "key": "d" * 64,
            "owner": uid(2),
            "team": uid(3),
            "project": None,
        }
        atomic_json(self.home / "native-tasks.json", self.grant)
        self.store = Store(self.home, clock=lambda: self.clock)
        for slug, app in (("qui-gon", 1000), ("dexter", 1001)):
            self.store.install(
                slug, uid(app), uid(1), "fixture-access", "fixture-refresh", NOW + 86400
            )
        self.linear = NativeLinear()
        self.service = Service(
            self.home,
            store=self.store,
            linear=self.linear,
            hermes=FakeHermes(),
            clock=lambda: self.clock,
        )
        self.intake = self.service.intake
        self.request = {
            "action": "create",
            "key": "weekly-travel",
            "title": "Weekly travel review",
            "description": "Research the requested week and review one shared report.",
        }

    def test_one_coordinator_task_as_app_without_a_human_assignee(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.intake.execute(self.request), range(4)))
        self.assertEqual(len({r["issue"] for r in replies}), 1)
        self.assertEqual(replies[0]["state"], "queued")
        self.service.step()
        self.assertEqual(self.linear.creates, 1)
        self.assertEqual(self.linear.created_as, "qui-gon")
        self.assertEqual(self.linear.created_fields["delegateId"], uid(1000))
        self.assertIsNone(self.linear.created_fields["assigneeId"])
        self.assertNotIn("creatorId", self.linear.created_fields)
        result = self.intake.execute({"action": "get", "key": self.request["key"]})
        self.assertEqual(result["execution"]["state"], "running")
        self.assertEqual(len(self.service.hermes.requests), 1)
        restarted = Service(
            self.home,
            store=self.store,
            linear=self.linear,
            hermes=self.service.hermes,
            clock=lambda: self.clock,
        )
        restarted.intake.execute(self.request)
        restarted.step()
        self.assertEqual(self.linear.creates, 1)
        self.assertEqual(len(self.service.hermes.requests), 1)

    def test_unknown_or_changed_requests_cannot_grant_app_work(self):
        self.intake.execute(self.request)
        with self.assertRaises(PermissionError):
            self.intake.execute({**self.request, "description": "Changed scope"})
        for fields in ({"assigneeId": uid(2)}, {"team": uid(99)}, {"delegate": "dexter"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.intake.execute({**self.request, **fields})
        registry = self.service.registry()
        self.linear.sessions[uid(601)] = session(owner=0, sid=601, creator=1000)
        with self.assertRaises(PermissionError):
            self.service.authorize(registry, registry.agents["qui-gon"], uid(601))

    def test_long_brief_is_rejected_before_admission_and_same_key_can_be_corrected(self):
        request = {**self.request, "description": "я" * 1201}
        with self.assertRaisesRegex(ValueError, "1200"):
            self.intake.execute(request)
        self.assertIsNone(self.intake.row(request["key"]))
        self.assertEqual(self.linear.creates, 0)
        self.assertFalse(self.store.jobs())
        self.assertFalse(self.service.hermes.requests)

        request["description"] = "я" * 1200
        result = self.intake.execute(request)
        self.assertEqual(self.intake.execute(request), result)
        self.service.step()
        self.assertEqual(self.linear.creates, 1)
        self.assertEqual(self.linear.created_fields["description"], request["description"])

    def test_existing_legacy_long_brief_replays_without_readmitting_or_rewriting(self):
        request = {**self.request, "description": "я" * 1500}
        with patch("linear_agent.intake.check_brief"):
            receipt = self.intake.execute(request)
        self.assertEqual(self.intake.execute(request), receipt)
        self.service.step()
        self.assertEqual(self.intake.execute(request)["issue"], receipt["issue"])
        self.assertEqual(self.linear.creates, 1)
        self.assertEqual(len(self.service.hermes.requests), 1)
        with self.assertRaises(PermissionError):
            self.intake.execute({**request, "description": request["description"] + "changed"})
        self.assertEqual(self.linear.created_fields["description"], request["description"])

    def test_scope_and_owner_changes_revoke_native_admission(self):
        first = self.intake.execute(self.request)
        registry = self.service.registry()
        self.assertIsNone(
            self.intake.authorized(registry, registry.agents["dexter"], first["issue"])
        )
        atomic_json(self.home / "native-tasks.json", {**self.grant, "owner": uid(999)})
        with self.assertRaises(PermissionError):
            self.intake.execute({"action": "list"})
        self.intake.reconcile(registry)
        self.assertEqual(self.linear.creates, 0)
        atomic_json(self.home / "native-tasks.json", {**self.grant, "project": uid(300)})
        self.assertIsNone(
            self.intake.authorized(registry, registry.agents["qui-gon"], first["issue"])
        )

    def test_uncertain_create_reuses_same_issue_without_duplicate_execution(self):
        self.linear.lose_create_reply = True
        first = self.intake.execute(self.request)
        self.service.step()
        self.assertEqual(self.intake.row(self.request["key"])["error"], "linear_unavailable")
        self.linear.lose_create_reply = False
        self.clock += 20
        self.service.step()
        self.assertEqual(self.linear.creates, 1)
        self.assertEqual(self.intake.row(self.request["key"])["issue"], first["issue"])
        self.assertEqual(len(self.service.hermes.requests), 1)

    def test_issue_identity_change_is_blocked_and_inspectable(self):
        first = self.intake.execute(self.request)
        registry = self.service.registry()
        self.linear.records[first["issue"]] = {
            "id": first["issue"],
            "team": {"id": uid(3)},
            "creator": {"id": uid(2)},
            "delegate": {"id": uid(1000)},
        }
        self.intake.reconcile(registry)
        reply = self.intake.execute({"action": "get", "key": self.request["key"]})
        self.assertEqual((reply["state"], reply["error"]), ("blocked", "identity_conflict"))
        self.assertFalse(self.store.jobs())

    def test_failed_admission_has_bounded_retries_and_visible_error(self):
        self.intake.execute(self.request)

        def unavailable(*_args):
            raise RemoteError()

        self.linear.recurring_issue = unavailable
        for _ in range(12):
            self.intake.reconcile(self.service.registry())
            self.clock += 400
        row = self.intake.row(self.request["key"])
        self.assertEqual(row["attempts"], 8)
        self.assertEqual(row["state"], "blocked")
        self.assertEqual(
            self.intake.execute({"action": "list"})["tasks"][0]["error"], "linear_unavailable"
        )

    def test_full_queue_does_not_lose_a_created_task(self):
        self.intake.execute(self.request)
        with patch.object(self.store, "admit", side_effect=BlockingIOError):
            self.intake.reconcile(self.service.registry())
        row = self.intake.row(self.request["key"])
        self.assertEqual(
            (row["state"], row["session"], row["error"]), ("queued", "", "capacity_unavailable")
        )
        self.clock += 20
        self.service.step()
        self.assertEqual(self.linear.creates, 1)
        self.assertEqual(len(self.service.hermes.requests), 1)

    def test_native_reply_is_idempotent_and_uses_existing_session(self):
        self.intake.execute(self.request)
        self.service.step()
        original = self.store.jobs()[0]
        self.store.update_job(original["key"], state="done")
        reply = {
            "action": "reply",
            "key": self.request["key"],
            "message_key": "clarification-1",
            "text": "Include domestic OTA.",
        }
        self.assertTrue(self.intake.execute(reply)["accepted"])
        self.assertFalse(self.intake.execute(reply)["accepted"])
        with self.assertRaises(ValueError):
            self.intake.execute({**reply, "text": "Different clarification"})
        self.service.step()
        self.assertEqual(len(self.service.hermes.requests), 2)
        self.assertEqual(len({r["session"] for r in self.service.hermes.requests.values()}), 1)

    def test_native_entry_has_its_own_internal_credential(self):
        for internal in (False, True):
            server = Server(("127.0.0.1", 0), self.service, internal=internal)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                path = (
                    "/coordinator/tasks" if internal else "/integrations/linear/coordinator/tasks"
                )
                for key in (None, "1" * 64, self.grant["key"]):
                    headers = {"Content-Type": "application/json"}
                    if key:
                        headers["Authorization"] = "Bearer " + key
                    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                    connection.request("POST", path, body='{"action":"list"}', headers=headers)
                    response = connection.getresponse()
                    self.assertEqual(
                        response.status,
                        (200 if key == self.grant["key"] else 401) if internal else 404,
                    )
                    response.read()
                    connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_native_answer_resumes_waiting_run_and_retry_survives_completion(self):
        self.intake.execute(self.request)
        self.service.step()
        original = self.store.jobs()[0]
        self.service.hermes.runs[original["run_id"]]["pending_controls"] = [
            {"kind": "clarify", "prompt_id": "which-market", "question": "Which market?"}
        ]
        self.clock += 31
        self.service.step()
        self.assertEqual(self.store.job(original["key"])["state"], "waiting")
        reply = {
            "action": "reply",
            "key": self.request["key"],
            "message_key": "answer-1",
            "text": "Detail. " * 1000,
        }
        self.assertTrue(self.intake.execute(reply)["accepted"])
        self.service.step()
        self.assertEqual(len(self.service.hermes.requests), 1)
        self.assertEqual(self.service.hermes.answers[0][2], reply["text"])
        self.assertFalse(self.intake.execute(reply)["accepted"])
        with self.assertRaises(ValueError):
            self.intake.execute({**reply, "text": "Air travel too."})

    def test_specialist_is_a_child_of_the_app_coordinator(self):
        parent_issue = self.intake.execute(self.request)["issue"]
        self.service.step()
        job = self.store.jobs()[0]
        parent = self.store.session("qui-gon", job["session"])
        result = self.service.delegate(
            "qui-gon",
            {
                "run": job["run_id"],
                "session": parent["hermes"],
                "target": "dexter",
                "key": "research",
                "title": "Lodging research",
                "description": "Inspect sources.",
            },
        )
        child = self.store.children("qui-gon", parent["id"])[0]
        self.assertEqual(child["parent_issue"], parent_issue)
        self.assertEqual(result["issue_id"], child["issue"])
        bound, _ = self.service.authorize(
            self.service.registry(), self.service.registry().agents["dexter"], uid(11)
        )
        self.assertEqual(bound["depth"], 1)
        self.assertEqual(bound["parent_session"], parent["id"])
        self.assertEqual(len(self.store.children("qui-gon", parent["id"])), 1)

    def test_disabled_or_revoked_grant_cannot_start_work(self):
        self.intake.execute(self.request)
        (self.home / "runtime").mkdir(exist_ok=True)
        (self.home / "runtime/maintenance").touch()
        self.assertEqual(len(self.intake.execute({"action": "list"})["tasks"]), 1)
        self.assertEqual(
            self.intake.execute({"action": "get", "key": self.request["key"]})["state"],
            "queued",
        )
        with self.assertRaises(BlockingIOError):
            self.intake.execute(self.request)
        with self.assertRaises(BlockingIOError):
            self.intake.execute({"action": "reply", "key": self.request["key"]})
        self.intake.reconcile(self.service.registry())
        self.assertEqual(self.linear.creates, 0)
        (self.home / "runtime/maintenance").unlink()
        value = json.loads((self.home / "agents.json").read_text())
        value["revision"] = "e" * 32
        atomic_json(self.home / "agents.json", value)
        self.intake.reconcile(self.service.registry())
        self.assertEqual(self.linear.creates, 0)
        self.assertEqual(self.intake.execute({"action": "list"})["tasks"], [])


if __name__ == "__main__":
    unittest.main()
