import fcntl
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from council_ops.config import OperationError
from council_ops.monitoring import configure, literal_value, notify, poll


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        home = root / "hermes"
        home.mkdir()
        (home / "telegram-owner.json").write_text('{"telegram_id":12345}')
        (home / ".env").write_text(
            'TELEGRAM_BOT_TOKEN="12345:' + "x" * 35 + '"\nOTHER_SECRET=$literal\n'
        )
        (home / ".env").chmod(0o600)
        self.settings = SimpleNamespace(
            data_dir=home, env_file=root / "compose.env", values={"HERMES_UID": str(os.getuid())}
        )
        self.runtime = SimpleNamespace(settings=self.settings)
        with (
            patch("council_ops.monitoring.backup_status", return_value={"configured": True}),
            patch("council_ops.monitoring.instruction_status", return_value={"configured": True}),
        ):
            configure(self.runtime)
        self.report = {
            "status": "running",
            "health": "healthy",
            "secretary": {"enabled": False},
            "backups": {
                "configured": True,
                "last_verified": "fixture",
                "age_seconds": 1,
                "timer": {"ActiveState": "active"},
            },
            "instructions": {
                "available": True,
                "local_changes": False,
                "timer": {"ActiveState": "active"},
            },
        }
        self.sender = Mock()

    def check(self):
        with patch(
            "council_ops.monitoring.shutil.disk_usage",
            return_value=SimpleNamespace(free=20 * 1024**3, total=50 * 1024**3),
        ):
            return poll(
                self.settings, sender=self.sender, probe=lambda: self.report, clock=lambda: 1000
            )

    def test_failure_is_deduplicated_across_polls_and_recovery_is_reported_once(self):
        self.assertFalse(self.check()["notification_sent"])
        self.report["backups"]["age_seconds"] = 40 * 3600
        self.check()
        self.check()
        self.assertEqual(self.sender.call_count, 1)
        self.assertIn("R2", self.sender.call_args.args[2])
        self.report["backups"]["age_seconds"] = 0
        self.check()
        self.check()
        self.assertEqual(self.sender.call_count, 2)
        self.assertIn("снова в порядке", self.sender.call_args.args[2])

    def test_unconfirmed_notification_is_retried_without_losing_the_episode(self):
        self.report["health"] = "unhealthy"
        self.sender.side_effect = OperationError("Transport failed")
        with self.assertRaises(OperationError):
            self.check()
        self.assertFalse(self.settings.env_file.with_name("monitor-state.json").exists())
        self.sender.side_effect = None
        self.check()
        self.assertEqual(self.sender.call_count, 2)

    def test_instruction_reasons_are_distinct_and_do_not_expose_revision_details(self):
        cases = [
            ({"available": False}, "проверить инструкции"),
            ({"local_changes": True}, "сверить версии"),
            ({"rejected_revision": "a" * 40}, "не прошла проверку"),
            ({"timer": {"ActiveState": "inactive"}}, "включить расписание"),
            ({"service": {"Result": "exit-code"}}, "причину сбоя"),
        ]
        healthy = self.report["instructions"].copy()
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.report["instructions"] = {**healthy, **changes}
                self.check()
                message = self.sender.call_args.args[2]
                self.assertIn(expected, message)
                self.assertNotIn("a" * 40, message)
                calls = self.sender.call_count
                self.check()
                self.assertEqual(self.sender.call_count, calls)
        self.report["instructions"] = healthy
        self.check()
        self.assertEqual(
            self.sender.call_args.args[2], "Автообновление инструкций снова в порядке."
        )
        calls = self.sender.call_count
        self.check()
        self.assertEqual(self.sender.call_count, calls)

    def test_new_rejected_revision_opens_a_new_notice_without_disclosing_hash(self):
        for revision in ("a" * 40, "b" * 40):
            self.report["instructions"]["rejected_revision"] = revision
            self.check()
            self.assertNotIn(revision, self.sender.call_args.args[2])
        self.assertEqual(self.sender.call_count, 2)

    def test_stopped_linear_executor_with_previous_healthy_state_is_reported(self):
        self.report["linear"] = {
            "configured": True,
            "available": True,
            "enabled": True,
            "registered_agents": 2,
            "installed_agents": 2,
            "services": {"dexter-executor": {"status": "exited", "health": "healthy"}},
        }
        self.check()
        self.assertEqual(self.sender.call_count, 1)
        self.assertIn("Linear", self.sender.call_args.args[2])

    def test_maintenance_lock_skips_checks_without_clearing_active_incidents(self):
        self.report["health"] = "unhealthy"
        self.check()
        path = self.settings.env_file.with_name("monitor-state.json")
        before = path.read_bytes()
        with self.settings.env_file.with_name("operations.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIn("monitoring_skipped", self.check())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.sender.call_count, 1)

    def test_recipient_is_an_immutable_root_pin_and_transport_errors_do_not_leak_token(self):
        configuration = json.loads(self.settings.env_file.with_name("monitor.json").read_text())
        (self.settings.data_dir / "telegram-owner.json").write_text('{"telegram_id":67890}')
        with self.assertRaises(OperationError), patch("urllib.request.build_opener") as opener:
            notify(self.settings, configuration, "Fixture")
        opener.assert_not_called()
        (self.settings.data_dir / "telegram-owner.json").write_text('{"telegram_id":12345}')
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = RuntimeError(
                "credential-bearing private exception"
            )
            with self.assertRaises(OperationError) as error:
                notify(self.settings, configuration, "Fixture")
        self.assertNotIn("credential-bearing", str(error.exception))
        self.assertNotIn("x" * 35, str(error.exception))

    def test_quoted_native_token_is_literal_and_symlinks_are_rejected(self):
        path = self.settings.data_dir / ".env"
        self.assertEqual(
            literal_value(path, "TELEGRAM_BOT_TOKEN", owner=os.getuid()), "12345:" + "x" * 35
        )
        path.rename(path.with_suffix(".real"))
        path.symlink_to(path.with_suffix(".real"))
        with self.assertRaises(OperationError):
            literal_value(path, "TELEGRAM_BOT_TOKEN", owner=os.getuid())
