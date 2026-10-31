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
        self.assertIsNone(self.linear.issue_records[result["id"]].get("parent"))
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
        self.assertEqual(len(self.linear.issue_records), 1)
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
        self.assertEqual(result["state"]["name"], "In Review")
        self.assertEqual(len(self.linear.comments), 1)
        self.call(context, "complete", key="done", result="Delivered", verification="Checked")
        self.assertEqual(len(self.linear.comments), 1)

    def test_long_completion_is_rejected_before_writing_and_can_be_rephrased(self):
        context = self.start()
        before = len(self.linear.issue_writes)
        for result, verification in (("x" * 501, "Checked"), ("Delivered", "x" * 161)):
            with self.subTest(result_length=len(result), verification_length=len(verification)):
                with self.assertRaisesRegex(ValueError, "save_document"):
                    self.call(
                        context, "complete", key="concise", result=result, verification=verification
                    )
                self.assertEqual(len(self.linear.issue_writes), before)
                self.assertFalse(self.linear.comments)
                self.assertEqual(self.call(context, "get")["state"]["type"], "started")
        result = (
            "Готов [материал](https://linear.app/document/result). Публикация не входила в задачу."
        )
        verification = "Редактура завершена; замечания устранены."
        self.call(context, "complete", key="concise", result=result, verification=verification)
        body = next(iter(self.linear.comments.values()))["body"]
        self.assertEqual(body, result + "\n\n**Проверено:** " + verification)
        self.assertLessEqual(len(body), 700)
        self.assertEqual(self.call(context, "get")["state"]["name"], "In Review")

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

    def test_only_owner_closes_reviewed_tasks_and_revisions_return_to_progress(self):
        context = self.start()
        self.call(context, "review", key="review", result="Ready", verification="Checked")
        for action in ("create", "update"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "Only the owner"):
                self.call(
                    context, action, key="bypass", fields={"title": "Text", "stateId": uid(203)}
                )
        with self.assertRaisesRegex(ValueError, "In Progress"):
            self.call(context, "update", key="revise", fields={"description": "Revised"})
        with self.assertRaisesRegex(ValueError, "In Progress"):
            self.call(
                context, "save_document", key="doc", fields={"title": "Text", "content": "Revised"}
            )
        self.call(context, "start", key="revision-start")
        self.assertEqual(self.call(context, "get")["state"]["name"], "In Progress")
        self.call(context, "update", key="revise", fields={"description": "Revised"})
        self.call(context, "complete", key="review-again", result="Revised", verification="Checked")
        self.assertEqual(self.call(context, "get")["state"]["name"], "In Review")

    def test_review_requires_a_real_state_and_allows_children_waiting_for_owner(self):
        context = self.start()
        workflow = self.linear.workflow(None, uid(3))
        self.linear.workflow = lambda *_args: workflow
        review = workflow["states"]["nodes"].pop()
        before = len(self.linear.issue_writes)
        with self.assertRaisesRegex(ValueError, "In Review state"):
            self.call(context, "review", key="ready", result="Ready", verification="Checked")
        self.assertEqual(len(self.linear.issue_writes), before)
        self.assertFalse(self.linear.comments)
        workflow["states"]["nodes"].append(review)
        self.linear.sessions[uid(10)]["issue"]["children"] = {
            "nodes": [{"id": uid(90), "state": review}],
            "pageInfo": {"hasNextPage": False},
        }
        self.call(context, "review", key="ready", result="Ready", verification="Checked")
        self.assertEqual(self.call(context, "get")["state"]["name"], "In Review")
        self.assertFalse(
            any(fields.get("stateId") == uid(203) for _, fields in self.linear.issue_writes)
        )

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

    def test_other_issue_outside_team_is_not_exposed_and_specialists_create_within_root(self):
        context = self.start()
        self.linear.issue_records[uid(99)] = copy.deepcopy(self.linear.sessions[uid(10)]["issue"])
        self.linear.issue_records[uid(99)]["team"] = {"id": uid(999)}
        with self.assertRaises(PermissionError):
            self.call(context, "get", issue=uid(99))
        session = self.store.context("qui-gon", context["run"], context["session"])
        session["agent"] = "dexter"
        original = self.store.context
        self.store.context = lambda *args: session
        self.service.authorize = lambda *args: (session, self.linear.sessions[uid(10)])
        try:
            result = self.service.issue_tasks(
                "dexter",
                {**context, "action": "create", "key": "shared", "fields": {"title": "Task"}},
            )
            self.assertEqual(self.linear.issue_records[result["id"]]["parent"]["id"], uid(20))
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

    def test_specialist_submits_subissue_for_review_but_cannot_finish_active_parent(self):
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
        self.assertEqual(result["state"]["name"], "In Review")
        with self.assertRaises(ValueError):
            self.call(
                context,
                "complete",
                key="too-soon",
                result="Parent done",
                verification="Sub-issue Done",
            )
        created = self.service.issue_tasks(
            "dexter",
            {
                **child_context,
                "action": "create",
                "key": "followup",
                "fields": {"title": "Shared follow-up"},
            },
        )
        self.assertEqual(self.linear.issue_records[created["id"]]["parent"]["id"], uid(20))
        parent = self.service.issue_tasks(
            "dexter", {**child_context, "action": "get", "issue": uid(20)}
        )
        self.assertEqual(parent["id"], uid(20))

    def test_specialists_share_project_content_and_writes_but_cannot_escape_project(self):
        context = self.start()
        current = self.linear.sessions[uid(10)]["issue"]
        current["project"] = {"id": uid(300)}
        sibling = copy.deepcopy(current)
        sibling.update(id=uid(99), title="Sibling", description="Shared source", parent=None)
        self.linear.issue_records[uid(99)] = sibling
        foreign = copy.deepcopy(sibling)
        foreign.update(id=uid(98), project={"id": uid(301)}, description="PRIVATE OTHER PROJECT")
        self.linear.issue_records[uid(98)] = foreign
        session = self.store.context("qui-gon", context["run"], context["session"])
        session["agent"] = "dexter"
        self.store.context = lambda *args: session
        self.service.authorize = lambda *args: (session, self.linear.sessions[uid(10)])

        def call(action, **params):
            return self.service.issue_tasks("dexter", {**context, "action": action, **params})

        self.assertEqual(call("get", issue=uid(99))["description"], "Shared source")
        call("update", issue=uid(99), key="edit", fields={"description": "Edited source"})
        self.assertEqual(self.linear.issue_records[uid(99)]["description"], "Edited source")
        listing = call("list")
        self.assertNotIn(uid(98), [r["id"] for r in listing["nodes"]])
        for action, params in (
            ("get", {"issue": uid(98)}),
            ("update", {"issue": uid(99), "key": "move", "fields": {"projectId": uid(301)}}),
            ("create", {"key": "new", "fields": {"title": "Wrong", "projectId": uid(301)}}),
        ):
            with self.subTest(action=action), self.assertRaises(PermissionError):
                call(action, **params)

    def test_no_project_siblings_share_root_but_unrelated_tasks_are_denied(self):
        from linear_agent.issues import Issues
        from linear_agent.project_scope import ProjectScope

        self.start()
        root = self.linear.issue_details(None, uid(20))
        for number in (80, 81, 82):
            self.linear.issue_records[uid(number)] = {
                **copy.deepcopy(root),
                "id": uid(number),
                "parent": {"id": uid(20)} if number != 82 else None,
            }
        scope = ProjectScope(
            Issues(self.service),
            self.service.registry().agents["dexter"],
            self.service.registry(),
            self.linear.issue_records[uid(80)],
        )
        scope.require(self.linear.issue_records[uid(81)])
        scope.require(root)
        with self.assertRaises(PermissionError):
            scope.require(self.linear.issue_records[uid(82)])

    def test_full_comment_and_document_reading_checks_scope_and_offsets(self):
        context = self.start()
        current = self.linear.sessions[uid(10)]["issue"]
        current["project"] = {"id": uid(300)}
        self.linear.issue_comments = lambda *args: {
            "nodes": [{"id": uid(70), "body": "x" * 13000}],
            "pageInfo": {"hasNextPage": False},
        }
        first = self.call(context, "comments")
        self.assertEqual(first["nodes"][0]["next_offset"], 12000)
        self.assertEqual(
            len(self.call(context, "comments", offset=12000)["nodes"][0]["body"]), 1000
        )
        self.linear.document = lambda *args: {
            "id": uid(71),
            "project": {"id": uid(301)},
            "content": "OTHER PROJECT",
        }
        with self.assertRaises(PermissionError):
            self.call(context, "document", document=uid(71))

    def test_document_writes_reconcile_lost_receipt_and_do_not_overwrite_newer_content(self):
        context = self.start()
        documents, writes = {}, []
        self.linear.document = lambda agent, identity: copy.deepcopy(documents.get(identity))

        def save(agent, identity, fields, *, create=False):
            writes.append(identity)
            documents.setdefault(
                identity,
                {
                    "id": identity,
                    "url": "https://linear.app/document/fixture",
                    "issue": {"id": fields.get("issueId", uid(20))},
                },
            ).update({k: v for k, v in fields.items() if k in {"title", "content"}})

        self.linear.save_document = save
        result = self.call(
            context,
            "save_document",
            key="document",
            fields={"title": "Deliverable", "content": "Reviewed text"},
        )
        self.assertEqual(
            self.call(
                context,
                "save_document",
                key="document",
                fields={"title": "Deliverable", "content": "Reviewed text"},
            ),
            result,
        )
        self.assertEqual(len(writes), 1)

        def lost(*args, **kwargs):
            save(*args, **kwargs)
            raise RemoteError()

        self.linear.save_document = lost
        with self.assertRaises(RemoteError):
            self.call(
                context,
                "save_document",
                key="edit",
                document=result["id"],
                fields={"title": "Deliverable", "content": "Updated"},
            )
        documents[result["id"]]["content"] = "Later human edit"
        self.linear.save_document = save
        with self.assertRaises(RemoteError):
            self.call(
                context,
                "save_document",
                key="edit",
                document=result["id"],
                fields={"title": "Deliverable", "content": "Updated"},
            )
        self.assertEqual(documents[result["id"]]["content"], "Later human edit")
