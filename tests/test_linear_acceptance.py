"""Coordinator acceptance is narrower than tracker edit permission."""

import json
import unittest

import test_linear_issues as issue_fixtures
from test_linear_agent import uid

from linear_agent.store import Store
from linear_agent.transport import RemoteError


class AcceptanceTests(unittest.TestCase):
    setUp = issue_fixtures.IssueManagementTests.setUp
    event = issue_fixtures.IssueManagementTests.event
    ingest = issue_fixtures.IssueManagementTests.ingest
    tick = issue_fixtures.IssueManagementTests.tick
    run_for = issue_fixtures.IssueManagementTests.run_for
    start = issue_fixtures.IssueManagementTests.start
    call = issue_fixtures.IssueManagementTests.call

    def ready(self):
        context = self.start()
        self.call(
            context,
            "review",
            key="review",
            result="Internal report",
            verification="Sources checked",
        )
        payload = {
            "key": "accept-report",
            "revision": self.call(context, "get")["acceptance"]["revision"],
            "category": "internal-research",
            "result": "Internal report meets the three requested criteria.",
            "verification": "Checked sources and the requested comparison.",
            "evidence": "https://www.notion.so/fixture-report",
            "artifact_version": "sha256:fixture-version",
        }
        return context, payload

    def test_accepts_ordinary_work_once_and_retains_auditable_decision(self):
        context, payload = self.ready()
        result = self.call(context, "accept", **payload)
        self.assertEqual(result["state"]["type"], "completed")
        self.assertEqual(result["acceptance"]["actor"], "qui-gon")
        self.assertEqual(result["acceptance"]["revision"], payload["revision"])
        before = len(self.linear.issue_writes), len(self.linear.comments)
        self.assertEqual(result, self.call(context, "accept", **payload))
        self.assertEqual(before, (len(self.linear.issue_writes), len(self.linear.comments)))
        later = Store(self.home, clock=lambda: self.store.clock() + 90 * 86400)
        with later.database() as db:
            record = dict(
                db.execute(
                    "SELECT * FROM issue_operations WHERE id=?", (result["decision_id"],)
                ).fetchone()
            )
        self.assertEqual(record["state"], "done")
        self.assertEqual(
            json.loads(record["request"])["artifact_version"], payload["artifact_version"]
        )

    def test_specialist_cannot_accept_even_with_project_edit_access(self):
        context, payload = self.ready()
        session = self.store.context("qui-gon", context["run"], context["session"])
        session["agent"] = "dexter"
        self.store.context = lambda *args: session
        self.service.authorize = lambda *args: (session, self.linear.sessions[uid(10)])
        with self.assertRaisesRegex(PermissionError, "root Qui-Gon"):
            self.service.issue_tasks("dexter", {**context, **payload, "action": "accept"})
        self.assertEqual(self.linear.sessions[uid(10)]["issue"]["state"]["name"], "In Review")

    def test_acceptance_comment_does_not_invalidate_the_checked_revision(self):
        context, payload = self.ready()
        issue = self.linear.sessions[uid(10)]["issue"]
        issue["comments"]["nodes"] = [
            {"id": uid(700 + i), "body": f"Detail {i}", "user": {"id": uid(2)}} for i in range(50)
        ]
        payload["revision"] = self.call(context, "get")["acceptance"]["revision"]
        create = self.linear.create_comment

        def append(agent, identity, target, body):
            create(agent, identity, target, body)
            issue["comments"]["nodes"].append(
                {"id": identity, "body": body, "user": {"id": uid(1000)}}
            )

        self.linear.create_comment = append
        result = self.call(context, "accept", **payload)
        self.assertEqual(result["state"]["type"], "completed")
        issue["state"] = self.linear.workflow(None, uid(3))["states"]["nodes"][-1]
        with self.assertRaisesRegex(ValueError, "reopened"):
            self.call(context, "accept", **payload)

    def test_important_reservation_survives_agent_reclassification(self):
        context, payload = self.ready()
        issue = self.linear.sessions[uid(10)]["issue"]
        issue["priority"] = 2
        self.call(context, "update", key="lower-priority", fields={"priority": 4})
        payload["revision"] = self.call(context, "get")["acceptance"]["revision"]
        with self.assertRaisesRegex(PermissionError, "high priority"):
            self.call(context, "accept", **payload)
        self.assertEqual(issue["state"]["name"], "In Review")

    def test_owner_only_categories_and_explicit_reservations(self):
        context, payload = self.ready()
        issue = self.linear.sessions[uid(10)]["issue"]
        for text in (
            "Опубликовать материал на сайте",
            "Deploy to production",
            "Изменить права доступа",
            "Увеличить бюджет",
            "Стратегический план",
            "Проверяю и принимаю я",
        ):
            with self.subTest(text=text):
                issue["description"] = text
                info = self.call(context, "get")["acceptance"]
                self.assertTrue(info["owner_required"])
                payload["revision"] = info["revision"]
                payload["key"] = "reserved-" + str(len(text))
                with self.assertRaises(PermissionError):
                    self.call(context, "accept", **payload)
        self.assertEqual(issue["state"]["name"], "In Review")

    def test_owner_label_cannot_be_removed_to_bypass_acceptance(self):
        context, payload = self.ready()
        issue = self.linear.sessions[uid(10)]["issue"]
        issue["labels"] = {"nodes": [{"id": uid(400), "name": "acceptance:owner"}]}
        self.call(context, "update", key="remove-label", fields={"labelIds": []})
        payload["revision"] = self.call(context, "get")["acceptance"]["revision"]
        with self.assertRaisesRegex(PermissionError, "label"):
            self.call(context, "accept", **payload)

    def test_changed_brief_or_owner_comment_requires_new_review(self):
        context, payload = self.ready()
        issue = self.linear.sessions[uid(10)]["issue"]
        issue["comments"]["nodes"].append(
            {"id": uid(700), "body": "Please add one comparison", "user": {"id": uid(2)}}
        )
        with self.assertRaisesRegex(ValueError, "changed after review"):
            self.call(context, "accept", **payload)
        self.assertEqual(issue["state"]["name"], "In Review")

    def test_lost_write_is_reconciled_without_repeating_or_overwriting_human_reopen(self):
        context, payload = self.ready()
        update = self.linear.update_issue

        def lost(*args):
            update(*args)
            raise RemoteError()

        self.linear.update_issue = lost
        with self.assertRaises(RemoteError):
            self.call(context, "accept", **payload)
        self.linear.update_issue = update
        before = len(self.linear.issue_writes)
        result = self.call(context, "accept", **payload)
        self.assertEqual(result["state"]["type"], "completed")
        self.assertEqual(len(self.linear.issue_writes), before)
        issue = self.linear.sessions[uid(10)]["issue"]
        issue["state"] = self.linear.workflow(None, uid(3))["states"]["nodes"][2]
        with self.assertRaisesRegex(ValueError, "In Review"):
            self.call(context, "accept", **payload)
        self.assertEqual(len(self.linear.issue_writes), before)

    def test_missing_evidence_unfinished_children_and_direct_done_are_rejected(self):
        context, payload = self.ready()
        for changes in (
            {"evidence": ""},
            {"artifact_version": ""},
            {"category": "production"},
            {"fields": {"stateId": uid(203)}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.call(context, "accept", **{**payload, **changes})
        issue = self.linear.sessions[uid(10)]["issue"]
        issue["children"]["nodes"].append(
            {"id": uid(600), "state": {"id": uid(205), "type": "started"}}
        )
        payload["revision"] = self.call(context, "get")["acceptance"]["revision"]
        with self.assertRaisesRegex(ValueError, "sub-issues"):
            self.call(context, "accept", **payload)
        with self.assertRaisesRegex(ValueError, "Direct Done"):
            self.call(context, "update", key="bypass", fields={"stateId": uid(203)})
