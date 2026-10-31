"""Native cron ownership and deterministic Notion-backed report delivery."""

import copy
import hashlib
import json
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path

from research_filing.store import Store
from telegram_monitor.delivery import report
from telegram_monitor.evidence import daily_window
from telegram_monitor.schedule import provision

NOW = datetime(2026, 10, 31, 5, tzinfo=UTC)


def _hermes_now():
    return NOW


def parse_schedule(value):
    return {"kind": "cron", "expr": value}


class Jobs:
    parse_schedule = staticmethod(parse_schedule)

    def __init__(self):
        self.values = {}

    def list_jobs(self, include_disabled=False):
        return copy.deepcopy(list(self.values.values()))

    def get_job(self, identity):
        return copy.deepcopy(self.values.get(identity))

    def create_job(self, **fields):
        identity = uuid.uuid4().hex
        job = {
            "id": identity,
            "skills": [],
            "script": None,
            "no_agent": False,
            **fields,
            "schedule": parse_schedule(fields["schedule"]),
            "enabled": not fields["paused"],
        }
        self.values[identity] = job
        return copy.deepcopy(job)

    def update_job(self, identity, updates):
        self.values[identity].update(updates)
        return self.get_job(identity)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        skill = self.home / "skills/travel-digest/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("Fixture skill")
        self.jobs = Jobs()

    def test_prepare_pause_activate_and_retry_never_create_duplicate_jobs(self):
        original = {"id": "unrelated", "name": "Existing owner job", "enabled": True}
        self.jobs.values["unrelated"] = copy.deepcopy(original)
        result = provision(self.home, "fixture_source", 20, 10, self.jobs)
        self.assertFalse(result["enabled"])
        identities = set(result["jobs"].values())
        self.assertTrue(all(not self.jobs.values[i]["enabled"] for i in identities))
        result = provision(self.home, "fixture_source", 20, 10, self.jobs, activate=True)
        self.assertEqual(set(result["jobs"].values()), identities)
        self.assertTrue(all(self.jobs.values[i]["enabled"] for i in identities))
        provision(self.home, "fixture_source", 20, 10, self.jobs, activate=True)
        self.assertEqual(len(self.jobs.values), 3)
        self.assertEqual(self.jobs.values["unrelated"], original)
        delivery = self.jobs.values[result["jobs"]["delivery"]]
        self.assertTrue(delivery["no_agent"])
        self.assertEqual(delivery["deliver"], "telegram:10")
        self.assertEqual(delivery["schedule"]["expr"], "0 5 * * *")

    def test_user_job_edits_script_edits_and_source_reassignment_are_preserved(self):
        result = provision(self.home, "fixture_source", 20, 10, self.jobs)
        identity = result["jobs"]["research"]
        self.jobs.values[identity]["prompt"] = "Owner customization"
        with self.assertRaises(ValueError):
            provision(self.home, "fixture_source", 20, 10, self.jobs, activate=True)
        self.assertEqual(self.jobs.values[identity]["prompt"], "Owner customization")
        with self.assertRaises(ValueError):
            provision(self.home, "fixture_source", 99, 10, self.jobs)
        script = self.home / "scripts/council-deliver-travel.py"
        script.write_text("Owner customization")
        with self.assertRaises(ValueError):
            provision(self.home, "fixture_source", 20, 10, self.jobs)
        self.assertEqual(script.read_text(), "Owner customization")

    def test_manual_pause_after_activation_is_not_silently_undone(self):
        result = provision(self.home, "fixture_source", 20, 10, self.jobs, activate=True)
        identity = result["jobs"]["research"]
        self.jobs.values[identity]["enabled"] = False
        provision(self.home, "fixture_source", 20, 10, self.jobs, activate=True)
        self.assertFalse(self.jobs.values[identity]["enabled"])


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.home, self.workspace = root / "home", root / "workspace"
        (self.home / ".council").mkdir(parents=True)
        (self.workspace / "reports").mkdir(parents=True)
        (self.workspace / "raw").mkdir()
        self.start, self.end = daily_window(NOW)
        self.day = self.end.date().isoformat()
        self.parent, self.page = str(uuid.uuid4()), str(uuid.uuid4())
        (self.home / "council-workspace.json").write_text(
            json.dumps({"knowledge_base": {"research": {"page_id": self.parent}}})
        )
        (self.home / ".council/telegram-monitor.json").write_text(
            json.dumps({"schema": 1, "enabled": True, "peer_id": 20})
        )
        self.ready = {
            "schema": 1,
            "window_start": self.start.isoformat(),
            "window_end": self.end.isoformat(),
            "task_id": "daily-task",
            "summary": "Verified research summary",
            "url": "https://untrusted.example",
        }
        self.ready_path = self.workspace / "reports" / (self.day + ".json")
        self.ready_path.write_text(json.dumps(self.ready))
        self.store = Store(self.home)
        self.filing = {
            "id": hashlib.sha256(b"research:daily-task").hexdigest(),
            "state": "done",
            "created_at": self.end.timestamp() + 60,
            "task_id": "daily-task",
            "date": self.day,
            "parent": self.parent,
            "page_id": self.page,
        }
        self.store.save(self.filing)

    def test_link_is_derived_from_verified_ledger_not_the_model_handoff(self):
        text = report(self.home, self.workspace, NOW)
        self.assertIn("https://www.notion.so/" + self.page, text)
        self.assertNotIn("untrusted.example", text)
        self.assertIn("источник недоступен", text)
        evidence = {
            "schema": 1,
            "peer_id": 20,
            "window_end": self.end.isoformat(),
            "status": "complete",
            "coverage": "full_text_window",
        }
        (self.workspace / "raw" / (self.day + ".json")).write_text(json.dumps(evidence))
        self.assertIn("вложения не анализировались", report(self.home, self.workspace, NOW))

    def test_pending_wrong_parent_old_report_and_stale_window_never_claim_filing(self):
        for change in (
            {"state": "queued"},
            {"parent": str(uuid.uuid4())},
            {"date": "2026-10-30"},
            {"created_at": self.end.timestamp() - 1},
        ):
            self.store.save({**self.filing, **change})
            with self.subTest(change=change), self.assertRaises(ValueError):
                report(self.home, self.workspace, NOW)
        self.store.save(self.filing)
        self.ready["window_end"] = self.start.isoformat()
        self.ready_path.write_text(json.dumps(self.ready))
        with self.assertRaises(ValueError):
            report(self.home, self.workspace, NOW)
