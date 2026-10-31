"""Real issue lifecycle is distinct from agent session completion and delegation."""

import concurrent.futures
import copy
import json
import threading
import unittest

import test_linear_agent as fixtures
from test_linear_agent import FakeLinear, uid

from linear_agent.service import Service
from linear_agent.transport import RemoteError


class IssueManagementTests(unittest.TestCase):
    setUp = fixtures.LinearAgentTests.setUp
    event = fixtures.LinearAgentTests.event
    ingest = fixtures.LinearAgentTests.ingest
    tick = fixtures.LinearAgentTests.tick
    run_for = fixtures.LinearAgentTests.run_for

    def start(self):
        self.ingest(self.event())
        self.tick()
        session = self.store.session("qui-gon", uid(10))
        return {"run": self.run_for(), "session": session["hermes"]}

    def call(self, context, action, **fields):
        return self.service.issue_tasks("qui-gon", {**context, "action": action, **fields})

    def test_ordinary_backlog_issue_is_not_an_executed_or_delegated_child(self):
        context = self.start()
        result = self.call(
            context,
            "create",
            key="backlog",
            fields={"title": "Future work", "priority": 2, "projectId": uid(300)},
        )
        self.assertEqual(result["state"]["type"], "backlog")
        self.assertEqual(result["priority"], 2)
        self.assertFalse(self.store.children("qui-gon", uid(10)))
        self.assertIsNone(self.linear.issues[result["id"]].get("parent"))
        self.assertEqual(
            self.call(
                context,
                "create",
                key="backlog",
                fields={"title": "Future work", "priority": 2, "projectId": uid(300)},
            ),
            result,
        )
        with self.assertRaises(ValueError):
            self.call(context, "create", key="backlog", fields={"title": "Different"})

    def test_lost_create_response_and_adapter_restart_reconcile_the_same_issue(self):
        context = self.start()
        create = self.linear.create_issue

        def lost(*args):
            create(*args)
            raise RemoteError()

        self.linear.create_issue = lost
        with self.assertRaises(RemoteError):
            self.call(context, "create", key="lost", fields={"title": "Future"})
        self.linear.create_issue = create
        self.service = Service(self.home, store=self.store, linear=self.linear, hermes=self.hermes)
        result = self.call(context, "create", key="lost", fields={"title": "Future"})
        self.assertEqual(len(self.linear.issues), 1)
        self.assertEqual(result["state"]["type"], "backlog")

    def test_begins_delegated_issue_but_not_a_comment_only_mention_or_terminal_issue(self):
        context = self.start()
        self.assertEqual(self.call(context, "get")["state"]["type"], "started")
        self.assertEqual(len(self.linear.issue_writes), 1)
        self.tick()
        self.assertEqual(len(self.linear.issue_writes), 1)
        for terminal in (None, "completed", "canceled"):
            with self.subTest(terminal=terminal):
                remote = self.linear.sessions[uid(10)]
                remote["issue"]["delegate"] = None if terminal is None else {"id": uid(1000)}
                remote["issue"]["state"] = next(
                    s
                    for s in self.linear.workflow(None, uid(3))["states"]["nodes"]
                    if s["type"] == (terminal or "backlog")
                )
                from linear_agent.issues import Issues

                Issues(self.service).begin_delegated(
                    self.service.registry().agents["qui-gon"], remote
                )
                self.assertEqual(len(self.linear.issue_writes), 1)

    def test_complete_requires_actual_result_verification_and_resolved_children(self):
        context = self.start()
        with self.assertRaises(ValueError):
            self.call(context, "complete", key="done", result="Delivered")
        with self.assertRaises(ValueError):
            self.call(context, "update", key="bypass", fields={"stateId": uid(203)})
        issue = self.linear.sessions[uid(10)]["issue"]
        issue["children"] = {
            "nodes": [{"id": uid(90), "state": {"type": "started"}}],
            "pageInfo": {"hasNextPage": False},
        }
        with self.assertRaises(ValueError):
            self.call(context, "complete", key="done", result="Delivered", verification="Checked")
        issue["children"]["nodes"][0]["state"]["type"] = "completed"
        result = self.call(
            context, "complete", key="done", result="Delivered", verification="Checked"
        )
        self.assertEqual(result["state"]["type"], "completed")
        self.assertEqual(len(self.linear.comments), 1)
        self.call(context, "complete", key="done", result="Delivered", verification="Checked")
        self.assertEqual(len(self.linear.comments), 1)

    def test_unknown_update_never_overwrites_a_later_human_edit(self):
        context = self.start()
        update = self.linear.update_issue

        def lost(*args):
            update(*args)
            raise RemoteError()

        self.linear.update_issue = lost
        with self.assertRaises(RemoteError):
            self.call(context, "update", key="priority", fields={"priority": 1})
        self.linear.update_issue = update
        self.linear.sessions[uid(10)]["issue"]["priority"] = 3
        with self.assertRaises(RemoteError):
            self.call(context, "update", key="priority", fields={"priority": 1})
        self.assertEqual(self.linear.sessions[uid(10)]["issue"]["priority"], 3)

    def test_known_lost_update_and_comment_receipts_are_recovered_without_repeating(self):
        context = self.start()
        update = self.linear.update_issue

        def lost(*args):
            update(*args)
            raise RemoteError()

        self.linear.update_issue = lost
        with self.assertRaises(RemoteError):
            self.call(context, "update", key="priority", fields={"priority": 1})
        self.linear.update_issue = update
        before = len(self.linear.issue_writes)
        self.assertEqual(
            self.call(context, "update", key="priority", fields={"priority": 1})["priority"], 1
        )
        self.assertEqual(len(self.linear.issue_writes), before)
        self.call(context, "comment", key="note", body="Next: coordinator reviews the result.")
        self.call(context, "comment", key="note", body="Next: coordinator reviews the result.")
        self.assertEqual(len(self.linear.comments), 1)

    def test_concurrent_retry_does_not_repeat_an_in_flight_update(self):
        context = self.start()
        entered, release = threading.Event(), threading.Event()
        update = self.linear.update_issue
        calls = []

        def delayed(*args):
            calls.append(args)
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test did not release the mutation")
            update(*args)

        self.linear.update_issue = delayed
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                self.call, context, "update", key="parallel", fields={"priority": 1}
            )
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(RemoteError):
                    self.call(context, "update", key="parallel", fields={"priority": 1})
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3)["priority"], 1)
        self.assertEqual(
            self.call(context, "update", key="parallel", fields={"priority": 1})["priority"], 1
        )
        self.assertEqual(len(calls), 1)

    def test_unauthorized_team_fields_and_policy_changes_are_rejected(self):
        context = self.start()
        for fields in (
            {"team": uid(999)},
            {"fields": {"title": "Task", "delegateId": uid(1001)}},
            {"fields": {"title": "Task", "projectId": uid(999)}},
            {"fields": {"title": "Task", "assigneeId": uid(1001)}},
        ):
            with self.subTest(fields=fields), self.assertRaises((PermissionError, ValueError)):
                self.call(context, "create", key="bad", **fields)
        self.config["revision"] = "b" * 32
        (self.home / "agents.json").write_text(json.dumps(self.config))
        with self.assertRaises(PermissionError):
            self.call(context, "create", key="stale", fields={"title": "Task"})

    def test_other_issue_outside_team_is_not_exposed_and_specialists_cannot_create(self):
        context = self.start()
        self.linear.issues[uid(99)] = copy.deepcopy(self.linear.sessions[uid(10)]["issue"])
        self.linear.issues[uid(99)]["team"] = {"id": uid(999)}
        with self.assertRaises(PermissionError):
            self.call(context, "get", issue=uid(99))
        session = self.store.context("qui-gon", context["run"], context["session"])
        session["agent"] = "dexter"
        original = self.store.context
        self.store.context = lambda *args: session
        self.service.authorize = lambda *args: (session, self.linear.sessions[uid(10)])
        try:
            with self.assertRaises(PermissionError):
                self.service.issue_tasks(
                    "dexter",
                    {**context, "action": "create", "key": "bad", "fields": {"title": "Task"}},
                )
        finally:
            self.store.context = original

    def test_comment_requires_a_body_and_completion_is_not_automatic_on_model_response(self):
        context = self.start()
        with self.assertRaises(ValueError):
            self.call(context, "comment", key="empty")
        self.hermes.finish(self.run_for(), "An explanation, not a verified deliverable.")
        self.tick(3)
        self.assertEqual(
            FakeLinear.issue_details(self.linear, None, uid(20))["state"]["type"], "started"
        )

    def test_specialist_closes_own_real_subissue_but_cannot_create_other_work(self):
        context = self.start()
        child = self.service.delegate(
            "qui-gon",
            {
                **context,
                "target": "dexter",
                "key": "research",
                "title": "Research",
                "description": "Verify sources",
            },
        )
        self.ingest(self.event(slug="dexter", sid=11, actor=0), slug="dexter")
        self.tick()
        own = self.store.session("dexter", uid(11))
        child_context = {"run": self.run_for("dexter"), "session": own["hermes"]}
        result = self.service.issue_tasks(
            "dexter",
            {
                **child_context,
                "action": "complete",
                "key": "complete",
                "result": "Research delivered",
                "verification": "Primary sources verified",
            },
        )
        self.assertEqual(result["id"], child["issue_id"])
        self.assertEqual(result["state"]["type"], "completed")
        for action in ("create", "list"):
            with self.subTest(action=action), self.assertRaises(PermissionError):
                self.service.issue_tasks(
                    "dexter",
                    {
                        **child_context,
                        "action": action,
                        "key": "other",
                        "fields": {"title": "Unrelated work"},
                    },
                )
