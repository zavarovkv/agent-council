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
from council_ops.guests import configure, require_compatible
from telegram_secretary.guest_policy import RELATIVE_POLICY, load_state


class GuestOperationsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.runtime = Mock(settings=SimpleNamespace(data_dir=self.home, values={}))
        self.runtime.inspect.return_value = {"Image": "image", "Config": {"Env": []}}
        self.runtime.run.return_value = json.dumps(
            [{"Image": "image", "State": {"Health": {"Status": "healthy"}}}]
        )

    def test_disabled_policy_keeps_compatibility_floor_and_revocation_needs_no_network(self):
        with patch("council_ops.guests.verify_capabilities") as verify:
            require_compatible(self.runtime, self.home, image="old")
            verify.assert_not_called()
            with patch("council_ops.guests.identity", return_value=(os.getuid(), os.getgid())):
                result = configure(self.runtime, enabled=False)
            self.assertFalse(result["enabled"])
            self.assertFalse(result["restart_required"])
            self.runtime.run.assert_not_called()
            first = load_state(self.home).revision
            configure(self.runtime, enabled=False)
            self.assertEqual(load_state(self.home).revision, first)
            require_compatible(self.runtime, self.home, image="old")
            verify.assert_called_once_with(
                self.runtime, ["telegram-guests-v1", "telegram-guest-owner-v1"], image="old"
            )
            self.assertEqual((self.home / RELATIVE_POLICY).stat().st_mode & 0o777, 0o600)

    def test_enable_requires_platform_and_matching_worker_before_checkpoint(self):
        with (
            patch("council_ops.guests.verify_capabilities"),
            patch(
                "council_ops.guests.SecretarySettings.load",
                return_value=SimpleNamespace(enabled=True),
            ),
            patch("council_ops.guests.platform_enabled", return_value=False) as platform,
            patch("council_ops.guests.snapshot", return_value=self.home / "checkpoint") as backup,
            patch("council_ops.guests.smoke") as smoke,
            patch("council_ops.guests.identity", return_value=(os.getuid(), os.getgid())),
        ):
            with self.assertRaises(OperationError):
                configure(self.runtime, enabled=True)
            backup.assert_not_called()
            self.assertFalse((self.home / RELATIVE_POLICY).exists())
            platform.return_value = True
            result = configure(self.runtime, enabled=True)
            self.assertTrue(result["enabled"])
            backup.assert_called_once_with(self.runtime, automatic=True)
            smoke.assert_called_once()
            copied = self.home / "copy"
            copied.mkdir()
            smoke.call_args.kwargs["prepare_copy"](copied)
            self.assertTrue(load_state(copied).enabled)
            self.assertEqual(load_state(copied).revision, load_state(self.home).revision)
            epoch = load_state(self.home).revision
            configure(self.runtime, enabled=False)
            self.assertNotEqual(load_state(self.home).revision, epoch)
            platform.assert_called()
            self.runtime.start.assert_not_called()

    def test_extended_policy_retains_compatibility_floor_after_disable(self):
        path = self.home / RELATIVE_POLICY
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"schema": 3, "enabled": True, "owner_agent": True, "revision": "a" * 32})
        )
        path.chmod(0o600)
        with patch("council_ops.guests.identity", return_value=(os.getuid(), os.getgid())):
            result = configure(self.runtime, enabled=False)
        self.assertFalse(result["enabled"])
        self.assertTrue(result["owner_agent"])
        self.assertEqual(load_state(self.home).schema, 3)
        with patch("council_ops.guests.verify_capabilities") as verify:
            require_compatible(self.runtime, self.home, image="old")
            self.assertIn("telegram-guest-agent-v1", verify.call_args.args[1])
        self.runtime.run.assert_not_called()

    def test_old_image_is_rejected_before_a_deployment_stops_anything(self):
        from council_ops.lifecycle import deploy

        path = self.home / RELATIVE_POLICY
        path.parent.mkdir(parents=True)
        path.write_text("invalid-but-still-a-compatibility-floor")
        with (
            patch("council_ops.lifecycle.build", return_value=("a" * 40, "old")),
            patch(
                "council_ops.guests.verify_capabilities", side_effect=OperationError("incompatible")
            ),
        ):
            with self.assertRaises(OperationError):
                deploy(self.runtime)
        self.runtime.stop.assert_not_called()
