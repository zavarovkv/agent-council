import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from test_operations import Fixture

from council_ops.doctor import backup_status, instruction_status, unit_status
from council_ops.instructions import Artifact, install_files


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
