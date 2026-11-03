"""Behavioral transport regressions: complete evidence, lineage, retries and blockers."""

import json
import unittest
from unittest.mock import patch

import test_linear_agent as fixture

from linear_agent.handoff import Handoffs, submission
from linear_agent.service import Service
from linear_agent.store import Store


class HandoffTests(unittest.TestCase):
    setUp = fixture.LinearAgentTests.setUp
    event = fixture.LinearAgentTests.event
    ingest = fixture.LinearAgentTests.ingest
    run_for = fixture.LinearAgentTests.run_for
    tick = fixture.LinearAgentTests.tick

    def start_tree(self, *, sibling=False):
        self.ingest(self.event())
        self.tick()
        parent = self.store.session("qui-gon", fixture.uid(10))
        self.service.delegate(
            "qui-gon",
            {
                "run": self.run_for(),
                "session": parent["hermes"],
                "target": "dexter",
                "key": "research",
                "title": "Research",
                "description": "Verify evidence",
            },
        )
        if sibling:
            self.store.reserve_child(parent, "dexter", "sibling", "Other research", "Still working")
        self.hermes.finish(self.run_for(), "Delegated")
        self.tick()
        self.ingest(self.event(slug="dexter", sid=11, actor=0), slug="dexter")
        self.tick()
        return self.store.session("dexter", fixture.uid(11))

    def context(self, slug="dexter"):
        session_id = fixture.uid(11 if slug == "dexter" else 10)
        session = self.store.session(slug, session_id)
        runs = [r for r in self.hermes.requests.values() if r["agent"] == slug]
        return {"run": runs[-1]["run_id"], "session": session["hermes"]}

    def submit(self, *, kind="result", **fields):
        return self.service.handoff(
            "dexter",
            {
                **self.context(),
                "action": "submit",
                "key": "report-v1",
                "kind": kind,
                "summary": "Findings verified.",
                "details": "Complete verified evidence.",
                **fields,
            },
        )

    def settle_child(self, text="Final pointer"):
        self.hermes.finish(self.context()["run"], text)
        self.tick(4)

    def get(self, event_id, **fields):
        return self.service.handoff(
            "qui-gon",
            {
                **self.context("qui-gon"),
                "action": "get",
                "event_id": event_id,
                **fields,
            },
        )

    def test_blocker_is_delivered_before_a_queued_scheduled_followup(self):
        self.start_tree()
        parent = self.store.session("qui-gon", fixture.uid(10))
        with self.store.database() as db:
            db.execute(
                "INSERT INTO jobs(key,agent,session,action,activity,actor,text,created,received,stop,revision,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "f" * 64,
                    "qui-gon",
                    parent["id"],
                    "scheduled_followup",
                    "",
                    parent["owner"],
                    "scheduled followup fixture",
                    self.now,
                    self.now,
                    0,
                    self.config["revision"],
                    self.now,
                ),
            )
        receipt = self.submit(kind="blocked", next_action="Restore access")
        self.settle_child()
        parent_runs = [r for r in self.hermes.requests.values() if r["agent"] == "qui-gon"]
        self.assertEqual(len(parent_runs), 2)
        self.assertIn(receipt["event_id"], parent_runs[-1]["text"])
        self.assertNotIn("scheduled followup fixture", parent_runs[-1]["text"])
        self.hermes.finish(parent_runs[-1]["run_id"], "Blocker understood")
        self.tick(4)
        parent_runs = [r for r in self.hermes.requests.values() if r["agent"] == "qui-gon"]
        self.assertEqual(len(parent_runs), 3)
        self.assertIn("scheduled followup fixture", parent_runs[-1]["text"])

    def test_new_handoff_revision_has_a_distinct_delivery_identity(self):
        self.start_tree()
        parent = self.store.session("qui-gon", fixture.uid(10))
        child = self.store.children("qui-gon", parent["id"])[0]
        child.update(state="blocked", result="A blocker", result_key="d" * 64)
        with patch.object(self.store, "handoff_id", return_value=None):
            self.store.notify_parent(parent, [child], self.config["revision"])
        with patch.object(self.store, "handoff_id", return_value="new-event"):
            self.store.notify_parent(parent, [child], self.config["revision"])
        with self.store.database() as db:
            keys = db.execute(
                "SELECT key FROM jobs WHERE agent='qui-gon' AND session=? AND action='children'",
                (parent["id"],),
            ).fetchall()
        self.assertEqual(len(keys), 2)
        self.assertNotEqual(keys[0][0], keys[1][0])

    def test_full_result_is_paged_without_public_or_parent_prompt_transcript(self):
        self.start_tree()
        details = "source-🙂-" * 2400
        receipt = self.submit(details=details)
        self.assertEqual(receipt, self.submit(details=details))
        self.settle_child("This final output is not the handoff summary.")
        child = self.store.children("qui-gon", fixture.uid(10))[0]
        self.assertEqual(child["result"], "Findings verified.")
        parent_text = [r for r in self.hermes.requests.values() if r["agent"] == "qui-gon"][-1][
            "text"
        ]
        self.assertIn(receipt["event_id"], parent_text)
        self.assertNotIn(details, parent_text)
        responses = [p["body"] for p in self.linear.posts.values() if p["kind"] == "response"]
        self.assertEqual(responses, ["Findings verified."])
        pages, offset, digests = [], 0, set()
        while True:
            page = self.get(receipt["event_id"], offset=offset)
            pages.append(page["details"])
            digests.add(page["digest"])
            offset = page["next_offset"]
            if offset is None:
                break
        self.assertEqual("".join(pages), details)
        self.assertEqual(digests, {receipt["digest"]})

    def test_legacy_result_beyond_old_16000_limit_survives_restart(self):
        self.start_tree()
        details = "Original evidence. " * 3000 + "END OF EVIDENCE"
        self.settle_child(details)
        self.service = Service(
            self.home,
            store=Store(self.home, clock=lambda: self.now),
            linear=self.linear,
            hermes=self.hermes,
            clock=lambda: self.now,
        )
        child = self.store.children("qui-gon", fixture.uid(10))[0]
        event_id = self.store.handoff_id(child["result_key"])
        self.assertEqual(self.store.job(child["result_key"])["output"], details)
        self.assertLessEqual(len(child["result"]), 700)
        restored, offset = "", 0
        while True:
            page = self.get(event_id, offset=offset)
            restored += page["details"]
            offset = page["next_offset"]
            if offset is None:
                break
        self.assertEqual(restored, details)
        self.assertFalse(any(details in p["body"] for p in self.linear.posts.values()))

    def test_blocker_wakes_parent_with_unfinished_sibling_and_resumes_same_session_once(self):
        self.start_tree(sibling=True)
        receipt = self.submit(
            kind="blocked",
            summary="Source permission missing.",
            next_action="Confirm access is restored.",
        )
        self.settle_child()
        child = next(
            c for c in self.store.children("qui-gon", fixture.uid(10)) if c["child_session"]
        )
        self.assertEqual(child["state"], "blocked")
        parents = [r for r in self.hermes.requests.values() if r["agent"] == "qui-gon"]
        self.assertEqual(len(parents), 2)
        self.assertIn('"state": "blocked"', parents[-1]["text"])
        self.service.authorize(
            self.service.registry(), self.service.registry().agents["dexter"], fixture.uid(11)
        )
        self.assertEqual(self.store.delegation("dexter", child["issue"])["state"], "blocked")
        request = {
            **self.context("qui-gon"),
            "action": "resume",
            "event_id": receipt["event_id"],
            "key": "access-fixed",
            "message": "Access restored and checked; retry the read.",
        }
        resumed = self.service.handoff("qui-gon", request)
        self.assertEqual(resumed, self.service.handoff("qui-gon", request))
        with self.assertRaises(ValueError):
            self.service.handoff("qui-gon", {**request, "message": "Changed instruction"})
        self.tick(3)
        child_runs = [r for r in self.hermes.requests.values() if r["agent"] == "dexter"]
        self.assertEqual(len(child_runs), 2)
        self.assertEqual(child_runs[0]["session"], child_runs[1]["session"])
        self.assertIn("Access restored", child_runs[-1]["text"])
        self.submit(summary="Source read and verified.")
        self.settle_child()
        self.assertEqual(self.store.delegation("dexter", child["issue"])["state"], "done")
        with self.store.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM handoff_resumes").fetchone()[0], 1)

    def test_identical_retry_survives_restart_and_other_key_or_payload_is_rejected(self):
        self.start_tree()
        first = self.submit()
        self.service = Service(
            self.home,
            store=Store(self.home, clock=lambda: self.now),
            linear=self.linear,
            hermes=self.hermes,
            clock=lambda: self.now,
        )
        self.assertEqual(first, self.submit())
        with self.assertRaises(ValueError):
            self.submit(summary="Changed after commit")
        with self.assertRaises(ValueError):
            self.submit(key="second-result")
        self.settle_child()
        self.tick(5)
        self.assertEqual(len([p for p in self.linear.posts.values() if p["kind"] == "response"]), 1)

    def test_forbidden_identity_cross_tree_read_and_resume_own_blocker(self):
        self.start_tree()
        with self.assertRaises(ValueError):
            self.submit(sender="qui-gon")
        receipt = self.submit(kind="blocked", next_action="Restore access")
        with self.assertRaises(PermissionError):
            self.service.handoff(
                "dexter",
                {
                    **self.context(),
                    "action": "resume",
                    "event_id": receipt["event_id"],
                    "key": "self",
                    "message": "Retry",
                },
            )
        self.settle_child()
        # A live, owner-authorized but unrelated root session cannot read this event.
        self.linear.sessions[fixture.uid(12)] = fixture.session(sid=12, issue=22)
        self.ingest(self.event(sid=12))
        self.tick()
        other = self.store.session("qui-gon", fixture.uid(12))
        with self.assertRaises(PermissionError):
            self.service.handoff(
                "qui-gon",
                {
                    "run": "",
                    "session": other["hermes"],
                    "action": "get",
                    "event_id": receipt["event_id"],
                },
            )
        self.linear.sessions[fixture.uid(11)]["issue"]["parent"] = {"id": fixture.uid(90)}
        with self.assertRaises(PermissionError):
            self.get(receipt["event_id"])

    def test_boundaries_reject_before_storage_and_return_explicit_quota_failure(self):
        self.start_tree()
        for fields in (
            {"summary": "s" * 501},
            {"details": "d" * 24001},
            {"kind": "blocked", "next_action": ""},
            {"limitations": "l" * 500, "summary": "s" * 500},
            {"artifacts": [{"url": "https://name:secret@example.test", "version": "1"}]},
        ):
            with self.subTest(fields=list(fields)), self.assertRaises(ValueError):
                self.submit(**fields)
        with self.store.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM handoffs").fetchone()[0], 0)
        with patch.object(
            Handoffs, "save", side_effect=ValueError("Handoff storage quota reached")
        ):
            self.settle_child()
        child = self.store.children("qui-gon", fixture.uid(10))[0]
        self.assertEqual(self.store.job(child["result_key"])["state"], "failed")
        self.assertEqual(child["state"], "blocked")
        self.assertTrue(
            any(
                "хранилище" in p["body"] for p in self.linear.posts.values() if p["kind"] == "error"
            )
        )

    def test_rendered_limit_and_unicode_are_counted_without_slicing(self):
        base = {
            "action": "submit",
            "key": "limit",
            "kind": "result",
            "summary": "🙂" * 500,
            "details": "evidence",
            "limitations": "x" * 186,
        }
        self.assertEqual(len(submission(base)["display"]), 700)
        with self.assertRaises(ValueError):
            submission({**base, "limitations": "x" * 187})

    def test_review_and_handoff_create_only_one_public_result(self):
        self.start_tree()
        self.service.issue_tasks(
            "dexter",
            {
                **self.context(),
                "action": "review",
                "key": "review-result",
                "result": "Findings verified.",
                "verification": "All evidence checked.",
            },
        )
        self.submit()
        self.settle_child()
        self.assertEqual(len(self.linear.comments), 1)
        self.assertFalse(any(p["kind"] == "response" for p in self.linear.posts.values()))
        self.assertEqual(self.store.children("qui-gon", fixture.uid(10))[0]["state"], "done")

    def test_failed_run_cannot_advertise_previously_submitted_result_as_success(self):
        self.start_tree()
        receipt = self.submit()
        self.hermes.runs[self.context()["run"]].update(
            status="failed", output="Unexpected failure after draft"
        )
        self.tick(4)
        result = self.get(receipt["event_id"])
        self.assertEqual(result["kind"], "blocked")
        self.assertEqual(result["run_state"], "failed")
        self.assertEqual(result["submitted_kind"], "result")
        self.assertEqual(self.store.children("qui-gon", fixture.uid(10))[0]["state"], "blocked")

    def test_failed_oversized_checkpoint_is_rejected_without_false_saved_claim(self):
        self.start_tree()
        self.hermes.runs[self.context()["run"]].update(status="failed", output="x" * 128001)
        self.tick(4)
        child = self.store.children("qui-gon", fixture.uid(10))[0]
        page = self.get(self.store.handoff_id(child["result_key"]))
        self.assertIn("не сохранён", page["details"])
        self.assertNotIn("x" * 100, page["details"])
        self.assertTrue(
            any(
                "не сохранён" in p["body"]
                for p in self.linear.posts.values()
                if p["kind"] == "error"
            )
        )

    def test_metadata_byte_budget_keeps_unicode_pages_within_tool_transport_limit(self):
        self.start_tree()
        artifacts = [{"url": "https://example.test/" + "🙂" * 2000, "version": "1"}] * 8
        with self.assertRaisesRegex(ValueError, "encoded bytes"):
            self.submit(artifacts=artifacts)
        receipt = self.submit(details="\x00" * 24000, artifacts=artifacts[:2])
        self.settle_child()
        response = self.get(receipt["event_id"])
        self.assertLess(len(json.dumps(response, ensure_ascii=False).encode()), 65536)

    def test_policy_revision_change_rejects_tool_mutation(self):
        self.start_tree()
        self.config["revision"] = "b" * 32
        (self.home / "agents.json").write_text(json.dumps(self.config))
        with self.assertRaises(PermissionError):
            self.submit()


if __name__ == "__main__":
    unittest.main()
