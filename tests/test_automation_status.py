import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from test_operations import Fixture

from council_ops.cli import pause_instruction_updates
from council_ops.doctor import (
    backup_status,
    generation_status,
    instruction_status,
    research_status,
    unit_status,
)
from council_ops.instructions import Artifact, install_files
from research_filing.store import Store
from secretary_worker.history import History
from secretary_worker.protocol import ReplyRequest


class AutomationStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = Fixture(temporary.name)

    def test_private_instruction_revision_and_drift_do_not_disclose_its_text(self):
        install_files(
            self.fixture.data,
            {"SOUL.md": Artifact(b"private identity phrase")},
            "d" * 40,
            os.getuid(),
            os.getgid(),
        )
        report = instruction_status(self.fixture.data)
        self.assertEqual(report["revision"], "d" * 40)
        self.assertFalse(report["local_changes"])
        (self.fixture.data / "SOUL.md").write_text("Edited identity")
        self.assertTrue(instruction_status(self.fixture.data)["local_changes"])
        self.assertNotIn("identity", json.dumps(report))

    def test_backup_readiness_distinguishes_credentials_from_verified_recovery(self):
        settings = self.fixture.settings()
        self.assertFalse(backup_status(settings)["configured"])
        env = settings.env_file.with_name("backup.env")
        env.write_text(
            "RESTIC_REPOSITORY=s3:https://"
            + "a" * 32
            + ".r2.cloudflarestorage.com/fixture-backups/production\nAWS_ACCESS_KEY_ID="
            + "b" * 32
            + "\nAWS_SECRET_ACCESS_KEY="
            + "c" * 64
            + "\nAWS_DEFAULT_REGION=auto\n"
        )
        env.chmod(0o600)
        password = settings.env_file.with_name("backup-password")
        password.write_text("fixture-private-recovery-password")
        password.chmod(0o600)
        report = backup_status(settings)
        self.assertTrue(report["configured"])
        self.assertIsNone(report["last_verified"])
        status = settings.env_file.with_name("last-cloud-backup.json")
        status.write_text(
            json.dumps({"verified_at": "2026-10-30T12:00:00+00:00", "snapshot": "e" * 64})
        )
        report = backup_status(settings)
        self.assertEqual(report["snapshot"], "e" * 64)
        for private in ("b" * 32, "c" * 64, "fixture-private-recovery-password"):
            self.assertNotIn(private, json.dumps(report))
        env.chmod(0o644)
        self.assertFalse(backup_status(settings)["configured"])

    def test_unit_diagnostics_keep_only_fixed_status_properties(self):
        runtime = SimpleNamespace(
            run=lambda _: "ActiveState=active\nUnitFileState=enabled\nResult=success\nEnvironment=private-value\nExecMainStatus=0\n"
        )
        self.assertEqual(
            unit_status(runtime, "agent-council-skills.timer"),
            {
                "ActiveState": "active",
                "UnitFileState": "enabled",
                "Result": "success",
                "ExecMainStatus": "0",
            },
        )

    def test_manual_rollback_pauses_only_the_owned_instruction_timer(self):
        calls = []

        def run(arguments):
            calls.append(arguments)
            return "ActiveState=active\nUnitFileState=enabled\n" if arguments[1] == "show" else ""

        pause_instruction_updates(SimpleNamespace(run=run))
        self.assertEqual(calls[-1], ["systemctl", "disable", "--now", "agent-council-skills.timer"])

    def test_generation_and_filing_diagnostics_read_only_safe_aggregate_fields(self):
        home = self.fixture.data
        history = History(home / ".council/secretary")
        request = ReplyRequest.parse(
            {
                "request_id": "a" * 64,
                "conversation": "b" * 64,
                "text": "private sender message",
                "route": "business",
            }
        )
        history.begin(request)
        history.failed(request.identifier, "provider_auth")
        generation = generation_status(home)
        self.assertEqual(generation["last_outcomes"]["business"]["outcome"], "provider_auth")
        self.assertEqual(generation["reserved_last_hour"]["business"], 1)
        (self.fixture.config / "research.json").write_text('{"schema":1}')
        store = Store(home)
        store.save(
            {
                "id": "fixture",
                "state": "indexing",
                "created_at": 1,
                "body": "private report content",
            }
        )
        before = store.path.read_bytes()
        filing = research_status(self.fixture.settings())
        self.assertEqual(filing["pending"], 1)
        self.assertEqual(before, store.path.read_bytes())
        for value in ("private sender message", "private report content", "a" * 64):
            self.assertNotIn(value, json.dumps({"generation": generation, "filing": filing}))
