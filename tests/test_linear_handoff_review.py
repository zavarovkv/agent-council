"""Independent regressions for retained lineage and failure evidence delivery."""

import concurrent.futures
import hashlib
import unittest

import test_linear_agent as core
import test_linear_handoffs as fixtures

from linear_agent.service import Service
from linear_agent.store import Store


class HandoffReviewTests(unittest.TestCase):
    setUp = fixtures.HandoffTests.setUp
    event = fixtures.HandoffTests.event
    ingest = fixtures.HandoffTests.ingest
    run_for = fixtures.HandoffTests.run_for
    tick = fixtures.HandoffTests.tick
    start_tree = fixtures.HandoffTests.start_tree
    context = fixtures.HandoffTests.context
    submit = fixtures.HandoffTests.submit
    settle_child = fixtures.HandoffTests.settle_child
    get = fixtures.HandoffTests.get

    def test_active_parent_can_read_retained_child_evidence_after_cleanup_deadlines(self):
        self.start_tree()
        receipt = self.submit(details="Evidence needed by the ongoing coordinator task.")
        self.settle_child()
        child = self.store.children("qui-gon", core.uid(10))[0]
        self.assertTrue(child["notified"])
        self.assertEqual(child["state"], "done")

        later = core.NOW + 31 * 86400
        self.store = Store(self.home, clock=lambda: later)
        self.service = Service(
            self.home,
            store=self.store,
            linear=self.linear,
            hermes=self.hermes,
            clock=lambda: later,
        )
        result = self.get(receipt["event_id"])
        self.assertEqual(result["details"], "Evidence needed by the ongoing coordinator task.")
        self.assertIsNotNone(self.store.delegation("dexter", child["issue"]))
        self.assertEqual(self.store.job(child["result_key"])["state"], "done")

    def test_failure_checkpoint_is_fully_readable_separately_from_immutable_submission(self):
        self.start_tree()
        submitted = "Verified findings submitted before the final operation failed."
        receipt = self.submit(details=submitted)
        checkpoint = "Partial source journal, unverified evidence 🙂. " * 800 + "CHECKPOINT END"
        self.hermes.runs[self.context()["run"]].update(status="failed", output=checkpoint)
        self.tick(4)

        original = self.get(receipt["event_id"])
        self.assertEqual(original["details"], submitted)
        self.assertEqual(original["digest"], receipt["digest"])
        self.assertEqual(original["kind"], "blocked")
        self.assertTrue(original["checkpoint_available"])
        self.assertGreater(original["checkpoint_chars"], len(checkpoint))

        pages, offset, content_digests = [], 0, set()
        while True:
            page = self.get(receipt["event_id"], view="checkpoint", offset=offset)
            self.assertEqual(page["revision"], receipt["event_id"] + ":checkpoint")
            self.assertEqual(page["digest"], receipt["digest"])
            self.assertEqual(page["total_chars"], original["checkpoint_chars"])
            self.assertLessEqual(len(page["details"]), 6000)
            pages.append(page["details"])
            content_digests.add(page["content_digest"])
            offset = page["next_offset"]
            if offset is None:
                break
        recovered = "".join(pages)
        self.assertTrue(recovered.endswith(checkpoint))
        self.assertEqual(len(recovered), original["checkpoint_chars"])
        self.assertEqual(content_digests, {hashlib.sha256(recovered.encode()).hexdigest()})
        self.assertGreater(len(pages), 1)
        self.assertFalse(
            any("CHECKPOINT END" in post["body"] for post in self.linear.posts.values())
        )
        self.assertEqual(self.get(receipt["event_id"])["details"], submitted)

    def test_concurrent_resume_retries_queue_exactly_one_child_continuation(self):
        self.start_tree()
        receipt = self.submit(kind="blocked", next_action="Restore access")
        self.settle_child()
        request = {
            **self.context("qui-gon"),
            "action": "resume",
            "event_id": receipt["event_id"],
            "key": "resolved",
            "message": "Access restored; continue the existing task.",
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.service.handoff("qui-gon", request), range(8)))
        self.assertTrue(all(result == results[0] for result in results))
        self.tick(3)
        child_runs = [run for run in self.hermes.requests.values() if run["agent"] == "dexter"]
        self.assertEqual(len(child_runs), 2)
        self.assertEqual(child_runs[0]["session"], child_runs[1]["session"])
        with self.store.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM handoff_resumes").fetchone()[0], 1)

    def test_replayed_finish_cannot_replace_queued_or_running_resume_with_old_blocker(self):
        self.start_tree()
        receipt = self.submit(kind="blocked", next_action="Restore access")
        self.settle_child()
        child = self.store.children("qui-gon", core.uid(10))[0]
        old_job = child["result_key"]
        resumed = self.service.handoff(
            "qui-gon",
            {
                **self.context("qui-gon"),
                "action": "resume",
                "event_id": receipt["event_id"],
                "key": "access-restored",
                "message": "Access restored; finish verification.",
            },
        )

        # A late receipt must not undo the explicit queued continuation, even
        # before a new executor run exists. It remains the same child session.
        self.assertEqual(self.store.job(resumed["job_key"])["state"], "queued")
        self.store.child_finished("dexter", core.uid(11), old_job)
        queued = self.store.delegation("dexter", child["issue"])
        self.assertEqual(queued["state"], "active")
        self.assertEqual(queued["result_key"], old_job)
        self.assertTrue(queued["notified"])

        self.tick()
        self.assertEqual(self.store.job(resumed["job_key"])["state"], "running")
        self.store.child_finished("dexter", core.uid(11), old_job)
        self.assertEqual(self.store.delegation("dexter", child["issue"])["state"], "active")
        self.hermes.finish(self.context("qui-gon")["run"], "The dependency is resolved.")
        self.tick()
        parent_jobs = [job for job in self.store.jobs() if job["agent"] == "qui-gon"]
        self.assertEqual([job["state"] for job in parent_jobs], ["waiting_children"])

        revised = self.submit(summary="Restored source read and verified.")
        self.settle_child()
        self.store.child_finished("dexter", core.uid(11), old_job)
        final = self.store.delegation("dexter", child["issue"])
        self.assertEqual(final["state"], "done")
        self.assertEqual(final["result_key"], resumed["job_key"])
        self.assertEqual(self.store.handoff_id(final["result_key"]), revised["event_id"])


if __name__ == "__main__":
    unittest.main()
