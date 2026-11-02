"""Business state, execution, responsibility and parent roll-up stay distinct."""

import copy
import unittest
from unittest.mock import patch

import test_linear_agent as fixtures
import test_linear_recovery as recovery_fixtures
from test_linear_agent import FakeLinear, uid

from linear_agent.transport import RemoteError
from linear_agent.workflow import first_state


class StatusLinear(FakeLinear):
    def workflow(self, agent, team):
        value = super().workflow(agent, team)
        value["states"]["nodes"].insert(
            -1, {"id": uid(207), "type": "started", "name": "Blocked", "position": -1}
        )
        return value


class TaskStatusTests(unittest.TestCase):
    event = fixtures.LinearAgentTests.event
    ingest = fixtures.LinearAgentTests.ingest
    tick = fixtures.LinearAgentTests.tick
    run_for = fixtures.LinearAgentTests.run_for
    family = recovery_fixtures.RecoveryTests.family

    def setUp(self):
        fixtures.LinearAgentTests.setUp(self)
        self.linear = self.service.linear = StatusLinear()

    def start(self):
        self.ingest(self.event())
        self.tick()
        return self.store.jobs()[0]

    def issue(self, sid=10):
        return self.linear.sessions[uid(sid)]["issue"]

    def test_blocked_is_never_selected_as_the_working_state(self):
        self.assertEqual(
            first_state(self.linear.workflow(None, uid(3)), "started")["name"], "In Progress"
        )

    def test_question_blocks_same_responsible_agent_and_answer_restores_progress(self):
        job = self.start()
        self.hermes.runs[job["run_id"]]["pending_controls"] = [
            {"kind": "clarify", "prompt_id": "question", "question": "Which period?"}
        ]
        self.tick()
        self.assertEqual(self.issue()["state"]["name"], "Blocked")
        status = self.service.task_status.describe(self.issue())
        self.assertEqual(status["responsible"]["id"], uid(1000))
        self.assertEqual(status["execution"]["waiting_on"], "owner")
        self.assertNotEqual(status["responsible"]["id"], uid(2))
        self.ingest(self.event(action="prompted", aid=61, text="October"))
        self.tick()
        self.assertEqual(self.issue()["state"]["name"], "In Progress")
        self.assertEqual(len(self.hermes.requests), 1)

    def test_failed_run_is_blocked_never_accepted_or_assigned_to_owner(self):
        job = self.start()
        self.hermes.runs[job["run_id"]].update(status="failed", output="Saved partial result")
        self.tick(3)
        self.assertEqual(self.issue()["state"]["name"], "Blocked")
        self.assertIsNone(self.issue().get("assignee"))
        self.assertEqual(
            self.service.task_status.describe(self.issue())["execution"]["state"], "failed"
        )

    def test_run_completion_does_not_mean_review_or_done(self):
        job = self.start()
        self.hermes.finish(job["run_id"])
        self.tick(3)
        self.assertEqual(self.issue()["state"]["name"], "Blocked")
        self.assertEqual(
            self.service.task_status.describe(self.issue())["acceptance"], "not_submitted"
        )

    def test_waiting_child_blocks_parent_without_changing_either_executor(self):
        self.family()
        self.hermes.runs[self.run_for("dexter")]["pending_controls"] = [
            {"kind": "clarify", "prompt_id": "question", "question": "Which source?"}
        ]
        self.tick()
        self.assertEqual(self.issue()["state"]["name"], "Blocked")
        self.assertEqual(self.issue(11)["state"]["name"], "Blocked")
        self.assertEqual(self.issue()["delegate"]["id"], uid(1000))
        self.assertEqual(self.issue(11)["delegate"]["id"], uid(1001))
        self.assertEqual(
            self.service.task_status.describe(self.issue())["execution"]["state"],
            "blocked_children",
        )

    def test_revisions_in_child_return_reviewed_parent_to_progress_but_not_accepted_parent(self):
        parent = self.family()
        child = next(j for j in self.store.jobs() if j["agent"] == "dexter")
        self.store.update_job(child["key"], state="done")
        self.store.update_job(parent["key"], state="done")
        review = self.linear.workflow(None, uid(3))["states"]["nodes"][-1]
        self.issue()["state"] = copy.deepcopy(review)
        self.service.task_status.reconcile(self.service.registry())
        self.store.update_job(child["key"], state="running")
        self.service.task_status.reconcile(self.service.registry())
        self.assertEqual(self.issue()["state"]["name"], "In Progress")
        self.issue()["state"] = {"id": uid(203), "name": "Done", "type": "completed"}
        self.hermes.runs[self.run_for("dexter")]["pending_controls"] = [
            {"kind": "clarify", "prompt_id": "question", "question": "Which source?"}
        ]
        self.tick()
        self.assertEqual(self.issue()["state"]["name"], "Done")

    def test_changed_delegate_and_manual_block_are_not_overwritten(self):
        job = self.start()
        self.issue()["state"] = {"id": uid(207), "name": "Blocked", "type": "started"}
        self.service.task_status.reconcile(self.service.registry())
        self.assertEqual(self.issue()["state"]["name"], "Blocked")
        self.issue()["delegate"] = {"id": uid(999)}
        self.store.update_job(job["key"], state="failed")
        before = len(self.linear.issue_writes)
        self.service.task_status.reconcile(self.service.registry())
        self.assertEqual(len(self.linear.issue_writes), before)
        self.assertEqual(self.issue()["delegate"]["id"], uid(999))

    def test_projection_does_not_poll_unchanged_tasks(self):
        self.start()
        with patch.object(self.linear, "task_status", wraps=self.linear.task_status) as remote:
            for _ in range(50):
                self.service.task_status.reconcile(self.service.registry())
            self.assertEqual(remote.call_count, 0)

    def test_restore_after_uncertain_write_before_and_after_provider_commit(self):
        for committed in (False, True):
            with self.subTest(committed=committed):
                job = self.start() if not self.store.jobs() else self.store.jobs()[0]
                self.store.update_job(job["key"], state="waiting")
                self.service.task_status.reconcile(self.service.registry())
                self.assertEqual(self.issue()["state"]["name"], "Blocked")
                self.store.update_job(job["key"], state="running")
                update = self.linear.update_issue

                def lost(*args, committed=committed, update=update):
                    if committed:
                        update(*args)
                    raise RemoteError(503)

                with patch.object(self.linear, "update_issue", side_effect=lost):
                    self.service.task_status.reconcile(self.service.registry())
                self.now += 400
                self.service.task_status.reconcile(self.service.registry())
                self.assertEqual(self.issue()["state"]["name"], "In Progress")

    def test_manually_reviewed_and_accepted_tasks_are_preserved_on_executor_failure(self):
        job = self.start()
        for state in (
            self.linear.workflow(None, uid(3))["states"]["nodes"][-1],
            {"id": uid(203), "type": "completed", "name": "Done"},
        ):
            self.issue()["state"] = state
            self.store.update_job(job["key"], state="failed", error=state["name"])
            self.service.task_status.reconcile(self.service.registry())
            self.assertEqual(self.issue()["state"], state)
