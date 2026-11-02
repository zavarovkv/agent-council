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
        remote = session(owner=1000, creator=1000)
        remote["id"] = sid
        remote["issue"]["id"] = fields["id"]
        self.sessions[sid] = remote
        self.records[fields["id"]] = {
            **remote["issue"],
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
        remote = session(owner=1000, creator=1000)
        self.linear.sessions[remote["id"]] = remote
        with self.assertRaises(PermissionError):
            self.service.authorize(
                self.service.registry(), self.service.registry().agents["qui-gon"], remote["id"]
            )

    def test_registered_session_can_delegate_and_reauth_preserves_stage(self):
        registry = self.service.registry()
        self.recurring.reconcile(registry)
        bound, _ = self.service.authorize(registry, registry.agents["qui-gon"], uid(600))
        self.assertEqual(bound["owner"], registry.owner)
        self.ready()
        self.service.authorize(registry, registry.agents["qui-gon"], uid(600))
        self.assertEqual(self.recurring.run(self.run["key"])["state"], "reviewed")

    def test_confirmed_report_is_delivered_once_and_receipt_settles(self):
        self.ready()
        first = self.render()
        self.assertIn("https://www.notion.so/", first)
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
