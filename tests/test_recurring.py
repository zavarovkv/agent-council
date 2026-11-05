import copy
import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_linear_agent import FakeHermes, FakeLinear, configuration, session, uid

from council_recurring.client import atomic_json
from council_recurring.contract import ZONE, definition, occurrence, revision, start_schedule
from council_recurring.runner import render, verified_page
from linear_agent.service import Service
from linear_agent.store import Store
from linear_agent.transport import RemoteError

NOW = datetime(2026, 11, 2, 8, 0, tzinfo=ZONE)
VALUE = {
    "id": "travel-digest",
    "title": "Travel дайджест",
    "brief": "Проверить изменения конкурентов.",
    "team": uid(3),
    "project": None,
    "notion_parent": "c" * 32,
    "start_time": "07:20",
    "cutoff_time": "07:15",
    "delivery_time": "08:00",
    "weekdays": list(range(7)),
    "window_days": 1,
    "telegram_source": True,
}


class RecurringLinear(FakeLinear):
    def __init__(self):
        super().__init__()
        self.records, self.creates = {}, 0
        self.lose_create_reply = False

    def recurring_issue(self, agent, identity):
        return self.records.get(identity)

    def create_issue(self, agent, fields):
        self.creates += 1
        sid = uid(600)
        remote = session(owner=0, creator=1000)
        remote["id"] = sid
        remote["issue"]["id"] = fields["id"]
        self.sessions[sid] = remote
        self.records[fields["id"]] = {
            **remote["issue"],
            "description": fields["description"],
            "agentSessions": {
                "nodes": [
                    {
                        "id": sid,
                        "appUser": {"id": uid(1000)},
                        "archivedAt": None,
                        "dismissedAt": None,
                    }
                ]
            },
        }
        if self.lose_create_reply:
            raise RemoteError()


class Executions:
    def __init__(self):
        self.current = "a" * 32
        self.previous = {}

    def list_executions(self, **_kwargs):
        return [{"id": self.current, "status": "running", "scheduled_instant": None}]

    def get_execution(self, identity):
        return self.previous.get(identity, {"id": identity, "status": "running"})


class RecurringTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve()
        self.clock = NOW.timestamp()
        configuration(self.home)
        atomic_json(self.home / "recurring.json", {"schema": 1, "key": "d" * 64, "owner": uid(2)})
        self.store = Store(self.home, clock=lambda: self.clock)
        for index, slug in enumerate(("qui-gon", "dexter")):
            self.store.install(
                slug,
                uid(1000 + index),
                uid(1),
                "fixture-access",
                "fixture-refresh",
                self.clock + 86400,
            )
        self.linear = RecurringLinear()
        self.service = Service(
            self.home,
            store=self.store,
            linear=self.linear,
            hermes=FakeHermes(),
            clock=lambda: self.clock,
        )
        self.recurring = self.service.recurring
        self.recurring.execute({"action": "configure", "definition": VALUE})
        self.run = self.recurring.execute({"action": "start", "id": VALUE["id"]})
        self.remote = lambda _home, payload: self.recurring.execute(payload)
        self.executions = Executions()
        atomic_json(
            self.home / ".council/recurring/jobs.json",
            {
                VALUE["id"]: {
                    "definition": VALUE,
                    "revision": revision(VALUE),
                    "enabled": True,
                    "jobs": {"start": "start", "delivery": "delivery"},
                }
            },
        )

    def page(self, args, **_kwargs):
        return json.dumps(
            {
                "id": "b" * 32,
                "parent": "c" * 32,
                "revision": "e" * 64,
                "url": "https://www.notion.so/" + "b" * 32,
                "content": "Reviewed " + self.run["key"],
                "next_offset": None,
            }
        )

    def ready(self):
        self.recurring.update(
            self.run["key"],
            state="reviewed",
            result=json.dumps(
                {"page": "b" * 32, "revision": "e" * 64, "summary": "Проверенный отчет."}
            ),
        )

    def render(self, **kwargs):
        return render(
            self.home,
            VALUE["id"],
            "delivery",
            native=None,
            executions=self.executions,
            remote=self.remote,
            workspace=kwargs.pop("workspace", self.page),
            now=kwargs.pop("now", NOW),
            **kwargs,
        )

    def submit_payload(self, *, skip_empty=True):
        # Configure the occurrence fixture before it starts; production snapshots
        # are immutable when a future schedule is edited.
        with self.store.database() as db:
            db.execute(
                "UPDATE recurring_runs SET body=? WHERE key=?",
                (json.dumps({**VALUE, "skip_empty": skip_empty}), self.run["key"]),
            )
        self.service.step()
        job = self.store.jobs()[0]
        self.linear.update_issue(None, self.run["issue"], {"stateId": uid(205)})
        return {
            "run": job["run_id"],
            "session": self.store.session("qui-gon", uid(600))["hermes"],
            "outcome": "no_updates",
            "summary": "Значимых новостей за период не найдено.",
            "verification": "Reviewed the completed source checks in the specialist handoff; no qualifying new event. Optional source gap retained.",
        }

    def test_verified_empty_check_is_silent_durable_and_not_overdue(self):
        payload = self.submit_payload()
        for _ in range(2):
            self.assertEqual(self.recurring.submit("qui-gon", payload)["delivery"], "not_required")
        for now in (NOW, datetime(2026, 11, 2, 13, 0, tzinfo=ZONE)):
            self.assertEqual(
                self.render(
                    now=now, workspace=lambda *_a, **_k: self.fail("No Notion read for no-news")
                ),
                "",
            )
        status = self.recurring.execute({"action": "status", "key": self.run["key"]})
        self.assertEqual(status["state"], "no_updates")
        self.assertNotIn("page", status["result"])
        self.assertEqual(status["deliveries"], [])
        self.assertFalse(
            self.recurring.problem(
                {"key": self.run["key"], "category": "deadline_exceeded"}, self.service.registry()
            )["recorded"]
        )
        claim = self.recurring.execute(
            {
                "action": "claim",
                "key": self.run["key"],
                "kind": "alert",
                "execution": "a" * 32,
                "body": "Stale failure alert",
            }
        )
        self.assertEqual(claim["body"], "")
        import shutil
        from types import SimpleNamespace

        from council_ops.recurring import status as health

        bridge = self.home / ".council/linear/bridge"
        bridge.mkdir(parents=True)
        shutil.copy2(self.home / "linear.sqlite3", bridge / "linear.sqlite3")
        self.assertEqual(
            health(
                SimpleNamespace(data_dir=self.home), now=datetime(2026, 11, 2, 8, 10, tzinfo=ZONE)
            )["overdue"],
            0,
        )
        restarted = Service(
            self.home,
            store=Store(self.home, clock=lambda: self.clock),
            linear=self.linear,
            hermes=FakeHermes(),
            clock=lambda: self.clock,
        )
        restarted.recurring.reconcile(restarted.registry())
        restarted.recurring.reconcile(restarted.registry())
        self.assertEqual(restarted.recurring.run(self.run["key"])["state"], "no_updates")
        self.assertEqual(
            len([j for j in restarted.store.jobs() if j["action"] == "scheduled_followup"]), 1
        )

    def test_empty_result_requires_owner_policy_and_coordinator_review(self):
        payload = self.submit_payload(skip_empty=False)
        with self.assertRaises(PermissionError):
            self.recurring.submit("qui-gon", payload)
        with self.assertRaises(PermissionError):
            self.recurring.submit("dexter", payload)
        with self.store.database() as db:
            db.execute(
                "UPDATE recurring_runs SET body=?", (json.dumps({**VALUE, "skip_empty": True}),)
            )
        for changed in ({"verification": ""}, {"page": "b" * 32}, {"outcome": "failed"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.recurring.submit("qui-gon", {**payload, **changed})
        self.linear.update_issue(None, self.run["issue"], {"stateId": uid(202)})
        with self.assertRaises(ValueError):
            self.recurring.submit("qui-gon", payload)
        self.linear.update_issue(None, self.run["issue"], {"stateId": uid(205)})
        parent = self.store.session("qui-gon", uid(600))
        self.store.reserve_child(parent, "dexter", "unfinished", "Research", "Check sources")
        with self.assertRaises(ValueError):
            self.recurring.submit("qui-gon", payload)
        self.assertNotEqual(self.recurring.run(self.run["key"])["state"], "no_updates")

    def test_no_news_cannot_replace_a_claimed_report(self):
        payload = self.submit_payload()
        self.ready()
        self.assertIn("https://www.notion.so", self.render())
        with self.assertRaises(ValueError):
            self.recurring.submit("qui-gon", payload)
        self.assertEqual(self.recurring.run(self.run["key"])["state"], "reviewed")

    def test_delivery_rechecks_empty_outcome_after_a_stale_read(self):
        from unittest.mock import patch

        payload = self.submit_payload()
        stale = self.recurring.run(self.run["key"])
        self.recurring.submit("qui-gon", payload)
        with patch.object(self.recurring, "run", return_value=stale):
            result = self.recurring.delivery(
                {
                    "action": "claim",
                    "key": self.run["key"],
                    "kind": "report",
                    "execution": "a" * 32,
                    "body": "Stale report",
                }
            )
        self.assertEqual(result["body"], "")
        self.assertEqual(
            self.recurring.execute({"action": "status", "key": self.run["key"]})["deliveries"], []
        )

    def test_report_submission_keeps_existing_page_contract(self):
        payload = self.submit_payload()
        payload.pop("outcome")
        payload.pop("verification")
        with self.assertRaises(ValueError):
            self.recurring.submit("qui-gon", payload)
        payload.update(page="b" * 32, revision="e" * 64)
        self.assertEqual(
            self.recurring.submit("qui-gon", payload)["delivery"],
            "pending_independent_verification",
        )
        self.assertIn("https://www.notion.so", self.render())

    def test_edit_inflight_schedule_preserves_original_edition_deadline_and_parent(self):
        changed = {**VALUE, "delivery_time": "09:00", "notion_parent": "d" * 32, "weekdays": [1]}
        self.recurring.execute({"action": "configure", "definition": changed})
        atomic_json(
            self.home / ".council/recurring/jobs.json",
            {
                VALUE["id"]: {
                    "definition": changed,
                    "revision": revision(changed),
                    "enabled": True,
                    "jobs": {"start": "start", "delivery": "delivery"},
                }
            },
        )
        self.ready()
        self.assertIn("https://www.notion.so/", self.render())

    def test_delivered_edition_creates_one_internal_acceptance_followup(self):
        registry = self.service.registry()
        self.recurring.reconcile(registry)
        self.recurring.update(self.run["key"], state="delivered")
        self.recurring.reconcile(registry)
        self.recurring.reconcile(registry)
        jobs = self.store.jobs()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(sum(j["action"] == "scheduled_followup" for j in jobs), 1)

    def test_invalid_optional_grant_does_not_stop_unrelated_adapter_work(self):
        (self.home / "recurring.json").write_text("{}")
        self.recurring.reconcile(self.service.registry())
        self.assertEqual(self.linear.creates, 0)

    def test_monitor_does_not_mistake_failure_alert_for_delivered_report(self):
        import shutil
        from types import SimpleNamespace

        from council_ops.recurring import status

        bridge = self.home / ".council/linear/bridge"
        bridge.mkdir(parents=True)
        self.render()
        self.recurring.execute(
            {
                "action": "settle",
                "key": self.run["key"],
                "kind": "alert",
                "execution": self.executions.current,
                "outcome": "delivered",
            }
        )
        shutil.copy2(self.home / "linear.sqlite3", bridge / "linear.sqlite3")
        result = status(
            SimpleNamespace(data_dir=self.home), now=datetime(2026, 11, 2, 8, 10, tzinfo=ZONE)
        )
        self.assertEqual(
            result,
            {"configured": True, "available": True, "expected": 1, "overdue": 1, "unreported": 0},
        )

    def test_maintenance_allows_health_but_blocks_new_scheduled_work(self):
        marker = self.home / "runtime/maintenance"
        marker.parent.mkdir(exist_ok=True)
        marker.write_text("maintenance")
        self.assertIn("runs", self.recurring.execute({"action": "list"}))
        self.recurring.reconcile(self.service.registry())
        self.assertEqual(self.linear.creates, 0)
        with self.assertRaises(BlockingIOError):
            self.recurring.execute({"action": "start", "id": VALUE["id"]})

    def test_moscow_cron_converts_day_when_crossing_midnight(self):
        self.assertEqual(start_schedule(VALUE), "20 4 * * 0,1,2,3,4,5,6")
        early = {**VALUE, "start_time": "01:20", "cutoff_time": "01:00", "weekdays": [0]}
        self.assertEqual(start_schedule(definition(early)), "20 22 * * 0")
        self.assertIn("+03:00", occurrence(VALUE, "2026-11-02")["window_end"])

    def test_duplicate_admission_preserves_one_issue_and_snapshot(self):
        changed = {**VALUE, "brief": "Новая версия задания."}
        self.recurring.execute({"action": "configure", "definition": changed})
        again = self.recurring.execute({"action": "start", "id": VALUE["id"]})
        self.assertEqual(again["issue"], self.run["issue"])
        self.assertEqual(json.loads(again["body"])["brief"], VALUE["brief"])
        self.assertEqual(len(self.recurring.rows()), 1)

    def test_lost_create_reply_recovers_same_issue_and_session(self):
        self.linear.lose_create_reply = True
        self.recurring.reconcile(self.service.registry())
        self.assertEqual(self.linear.creates, 1)
        self.clock += 30
        self.recurring.reconcile(self.service.registry())
        self.assertEqual(self.linear.creates, 1)
        self.assertEqual(self.recurring.run(self.run["key"])["session"], uid(600))
        self.assertEqual(len(self.store.jobs()), 1)

    def test_unregistered_application_session_cannot_gain_owner_rights(self):
        for owner in (0, 1000):
            with self.subTest(owner=owner):
                remote = session(owner=owner, creator=1000)
                self.linear.sessions[remote["id"]] = remote
                with self.assertRaises(PermissionError):
                    self.service.authorize(
                        self.service.registry(),
                        self.service.registry().agents["qui-gon"],
                        remote["id"],
                    )

    def test_automatic_session_starts_once_and_moves_issue_into_progress(self):
        self.service.step()
        self.service.step()
        job = self.store.jobs()[0]
        self.assertEqual(job["state"], "running")
        self.assertEqual(len(self.service.hermes.requests), 1)
        self.assertEqual(
            self.linear.issue_details(None, self.run["issue"])["state"]["name"], "In Progress"
        )

    def test_readable_issue_keeps_delivery_contract_in_executor_context(self):
        self.service.step()
        brief = self.linear.records[self.run["issue"]]["description"]
        self.assertIn(VALUE["brief"], brief)
        self.assertIn("01.11.2026 07:15", brief)
        self.assertIn("02.11.2026 07:15", brief)
        self.assertIn("08:00", brief)
        self.assertIn("https://www.notion.so/" + VALUE["notion_parent"], brief)
        self.assertNotIn("recurring_result", brief)
        request = next(iter(self.service.hermes.requests.values()))
        context = json.loads(request["text"])["recurring"]
        self.assertEqual(context["occurrence"], self.run["key"])
        self.assertEqual(context["deadline"], self.run["deadline"])
        self.assertEqual(context["window_start"], "2026-11-01T07:15:00+03:00")
        self.assertEqual(context["window_end"], "2026-11-02T07:15:00+03:00")
        self.assertEqual(context["notion_parent"], VALUE["notion_parent"])
        self.assertIn("recurring_result", context["instructions"])
        self.assertIn("Do not send Telegram", context["instructions"])

    def test_continuation_keeps_original_occurrence_after_schedule_edit(self):
        registry = self.service.registry()
        self.service.step()
        original_job = self.store.jobs()[0]
        self.store.update_job(original_job["key"], state="done")
        self.recurring.execute(
            {
                "action": "configure",
                "definition": {
                    **VALUE,
                    "cutoff_time": "07:00",
                    "delivery_time": "09:00",
                    "notion_parent": "d" * 32,
                },
            }
        )
        self.recurring.followup(self.recurring.run(self.run["key"]), registry, "test", "Continue")
        self.service.step()
        requests = list(self.service.hermes.requests.values())
        self.assertEqual(len(requests), 2)
        first, second = (json.loads(r["text"])["recurring"] for r in requests)
        self.assertEqual(first, second)
        self.assertEqual(json.loads(requests[1]["text"])["request"], "Continue")

    def test_context_requires_matching_coordinator_session_and_grant(self):
        registry = self.service.registry()
        self.recurring.reconcile(registry)
        for slug, issue, sid in (
            ("dexter", self.run["issue"], uid(600)),
            ("qui-gon", uid(999), uid(600)),
            ("qui-gon", self.run["issue"], uid(601)),
        ):
            with self.subTest(slug=slug, issue=issue, sid=sid):
                self.assertIsNone(
                    self.recurring.execution_context(registry, registry.agents[slug], issue, sid)
                )
        (self.home / "recurring.json").write_text("{}")
        with self.assertRaises(PermissionError):
            self.recurring.execution_context(
                registry, registry.agents["qui-gon"], self.run["issue"], uid(600)
            )

    def test_creatorless_webhook_requires_registered_schedule_and_current_session(self):
        registry = self.service.registry()
        self.recurring.reconcile(registry)
        payload = {
            "type": "AgentSessionEvent",
            "action": "created",
            "organizationId": registry.organization,
            "oauthClientId": registry.agents["qui-gon"].client_id,
            "appUserId": uid(1000),
            "createdAt": NOW.isoformat(),
            "webhookTimestamp": self.clock * 1000,
            "agentSession": {
                "id": uid(600),
                "issueId": self.run["issue"],
                "creatorId": None,
                "organizationId": registry.organization,
                "appUserId": uid(1000),
            },
        }

        def ingest(value):
            body = json.dumps(value).encode()
            signature = hmac.new(
                registry.agents["qui-gon"].webhook_secret.encode(), body, hashlib.sha256
            ).hexdigest()
            return self.service.ingest("qui-gon", body, signature)

        # Reconcile has already admitted this exact event; the webhook is a duplicate.
        with self.store.database() as db:
            db.execute("DELETE FROM jobs")
        # The fixture clock must be after the installation admission boundary.
        config = json.loads((self.home / "agents.json").read_text())
        config["admission_since"] = self.clock - 1
        atomic_json(self.home / "agents.json", config)
        self.assertTrue(ingest(payload))
        self.assertFalse(ingest(payload))
        for field, value in (("issueId", uid(999)), ("id", uid(601)), ("creatorId", uid(99))):
            with self.subTest(field=field):
                changed = copy.deepcopy(payload)
                changed["agentSession"][field] = value
                self.assertFalse(ingest(changed))

    def test_scheduled_session_rejects_changed_identity_and_revoked_grant(self):
        registry = self.service.registry()
        self.recurring.reconcile(registry)
        original = copy.deepcopy(self.linear.sessions[uid(600)])
        for target, field, value in (
            ("session", "creator", {"id": uid(99)}),
            ("session", "appUser", {"id": uid(99)}),
            ("issue", "creator", {"id": uid(99)}),
            ("issue", "delegate", {"id": uid(99)}),
            ("issue", "team", {"id": uid(99)}),
            ("issue", "id", uid(999)),
        ):
            with self.subTest(target=target, field=field):
                changed = copy.deepcopy(original)
                (changed["issue"] if target == "issue" else changed)[field] = value
                self.linear.sessions[uid(600)] = changed
                with self.assertRaises(PermissionError):
                    self.service.authorize(registry, registry.agents["qui-gon"], uid(600))
        self.linear.sessions[uid(600)] = original
        self.recurring.update(self.run["key"], state="cancelled")
        with self.assertRaises(PermissionError):
            self.service.authorize(registry, registry.agents["qui-gon"], uid(600))

    def test_registered_session_can_delegate_and_reauth_preserves_stage(self):
        registry = self.service.registry()
        self.recurring.reconcile(registry)
        bound, _ = self.service.authorize(registry, registry.agents["qui-gon"], uid(600))
        self.assertEqual(bound["owner"], registry.owner)
        self.ready()
        self.service.authorize(registry, registry.agents["qui-gon"], uid(600))
        self.assertEqual(self.recurring.run(self.run["key"])["state"], "reviewed")

    def test_confirmed_report_is_delivered_once_and_receipt_settles(self):
        summary = (
            "- **Первый:** Тариф 5% с 10 ноября, только для новых заказов.\n\n"
            "- **Второй:** Исправление заявлено, полное восстановление не подтверждено."
        )
        payload = self.submit_payload(skip_empty=False)
        payload.pop("verification")
        self.recurring.submit(
            "qui-gon",
            {
                **payload,
                "outcome": "report",
                "page": "b" * 32,
                "revision": "e" * 64,
                "summary": summary,
            },
        )
        first = self.render()
        self.assertEqual(first, summary + "\n\n[Подробнее](https://www.notion.so/" + "b" * 32 + ")")
        previous = self.executions.current
        self.executions.current = "b" * 32
        self.assertEqual(self.render(), "")
        self.executions.previous[previous] = {
            "status": "completed",
            "delivery_outcome": "delivered",
        }
        self.assertEqual(self.render(), "")
        self.assertEqual(self.recurring.run(self.run["key"])["state"], "delivered")

    def test_failure_notice_does_not_prevent_one_late_report(self):
        self.assertIn("пока не готов", self.render())
        alert = self.executions.current
        self.executions.previous[alert] = {"status": "completed", "delivery_outcome": "delivered"}
        self.executions.current = "b" * 32
        self.assertEqual(self.render(), "")
        self.ready()
        self.executions.current = "c" * 32
        self.assertIn(
            "Отчет готов с задержкой", self.render(now=datetime(2026, 11, 2, 8, 10, tzinfo=ZONE))
        )

    def test_unknown_send_is_not_blindly_replayed(self):
        self.ready()
        self.render()
        previous = self.executions.current
        self.executions.previous[previous] = {"status": "unknown", "delivery_outcome": "unknown"}
        self.executions.current = "b" * 32
        self.assertEqual(self.render(), "")
        status = self.remote(self.home, {"action": "status", "key": self.run["key"]})
        self.assertEqual(status["deliveries"][0]["state"], "unknown")

    def test_wrong_period_parent_or_changed_revision_never_delivers_report(self):
        self.ready()
        result = {"page": "b" * 32, "revision": "e" * 64}
        for field, value in (
            ("content", "yesterday"),
            ("parent", "f" * 32),
            ("revision", "f" * 64),
        ):
            with self.subTest(field=field):
                page = json.loads(self.page({}))
                page[field] = value
                self.assertIsNone(
                    verified_page(
                        lambda *_a, _page=page, **_k: json.dumps(_page),
                        self.home,
                        result,
                        {"key": self.run["key"]},
                        "c" * 32,
                    )
                )

    def test_owner_change_rejects_old_run_and_delivery(self):
        value = json.loads((self.home / "agents.json").read_text())
        value["owner"] = uid(5)
        atomic_json(self.home / "agents.json", value)
        with self.assertRaises(PermissionError):
            self.render()

    def test_no_send_before_deadline_and_no_large_backlog(self):
        self.ready()
        self.assertEqual(self.render(now=datetime(2026, 11, 2, 7, 45, tzinfo=ZONE)), "")
        with self.assertRaises(ValueError):
            self.remote(self.home, {"action": "start", "id": VALUE["id"], "day": "2026-10-01"})

    def test_repeated_failure_has_one_actionable_continuation(self):
        self.recurring.reconcile(self.service.registry())
        for _ in range(3):
            self.remote(
                self.home,
                {"action": "problem", "key": self.run["key"], "category": "artifact_unverified"},
            )
        self.assertEqual(
            len([j for j in self.store.jobs() if j["action"] == "scheduled_followup"]), 1
        )


if __name__ == "__main__":
    unittest.main()
