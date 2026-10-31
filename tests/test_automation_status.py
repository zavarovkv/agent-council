import fcntl
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from test_operations import Fixture

from council_ops.cli import main, pause_instruction_updates
from council_ops.config import OperationDeferred, OperationError
from council_ops.doctor import (
    backup_status,
    generation_status,
    instruction_status,
    research_status,
    unit_status,
)
from council_ops.instructions import Artifact, install_files
from council_ops.runtime import Runtime
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
            run=lambda _: (
                "ActiveState=active\nUnitFileState=enabled\nResult=success\nEnvironment=private-value\nExecMainStatus=0\n"
            )
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

    def test_background_timers_defer_a_real_busy_lock_without_constructing_runtime(self):
        settings = self.fixture.settings()
        with (
            settings.env_file.with_name("operations.lock").open("a") as lock,
            patch("council_ops.cli.Settings.load", return_value=settings),
            patch("council_ops.cli.os.geteuid", return_value=0),
            patch("council_ops.cli.Runtime") as runtime,
        ):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for argv in (["sync-instructions", "/private/source"], ["flush-research"]):
                with self.subTest(action=argv[0]), redirect_stdout(io.StringIO()) as output:
                    main(argv)
                    self.assertEqual(
                        json.loads(output.getvalue()),
                        {"operation_deferred": argv[0], "retry": "next_timer_tick"},
                    )
            with self.assertRaises(OperationDeferred):
                main(["deploy"])
            runtime.assert_not_called()

    def test_only_expected_busy_conditions_are_suppressed_for_repeating_timers(self):
        with (
            patch("council_ops.cli.Settings.load", return_value=self.fixture.settings()),
            patch("council_ops.cli.os.geteuid", return_value=0),
            patch("council_ops.cli.operate", side_effect=OperationDeferred("active work")),
            redirect_stdout(io.StringIO()) as output,
        ):
            main(["flush-research"])
            self.assertEqual(json.loads(output.getvalue())["retry"], "next_timer_tick")
            with self.assertRaises(OperationDeferred):
                main(["cloud-backup"])
        with (
            patch("council_ops.cli.Settings.load", return_value=self.fixture.settings()),
            patch("council_ops.cli.os.geteuid", return_value=0),
            patch("council_ops.cli.operate", side_effect=OperationError("invalid state")),
        ):
            with self.assertRaises(OperationError):
                main(["flush-research"])

    def test_active_work_defers_but_stale_or_invalid_readiness_still_fails(self):
        runtime = Runtime(self.fixture.settings())
        runtime.inspect = lambda: {"State": {"Running": True}}
        for status, expected in (
            ({"fresh": True, "agents": 1, "work": False}, OperationDeferred),
            ({"fresh": True, "agents": 0, "work": True}, OperationDeferred),
            ({"fresh": False, "agents": 0, "work": False}, OperationError),
            ({"fresh": True, "agents": None, "work": False}, OperationError),
        ):
            with self.subTest(status=status):
                runtime.compose = lambda *_args, status=status: json.dumps(status)
                with self.assertRaises(expected) as raised:
                    runtime.require_idle()
                self.assertIs(type(raised.exception), expected)

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
