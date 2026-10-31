import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from council_ops.config import OperationError, Settings, read_settings
from council_ops.lifecycle import deploy, restore, smoke
from council_ops.restic import Restic, prune_local
from council_ops.runtime import Runtime
from council_ops.snapshots import copy_state, load_manifest, snapshot

IMAGE = "sha256:" + "a" * 64
REVISION = "b" * 40


class Fixture:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.repository = self.root / "repository"
        self.config = self.root / "configuration"
        self.data = self.root / "state" / "hermes"
        self.workspace = self.root / "state" / "workspace"
        self.backups = self.root / "backups" / "council"
        for path in (self.repository, self.config, self.data, self.workspace):
            path.mkdir(parents=True)
        (self.repository / "compose.yaml").write_text("candidate Compose source\n")
        (self.config / "active-compose.yaml").write_text("previous Compose source\n")
        self.env = self.config / "compose.env"
        self.auth = self.config / "auth.env"
        self.auth.write_text("HERMES_TELEGRAM_CLIENT_SECRET=fake-secret-not-for-arguments\n")
        self.auth.chmod(0o600)
        self.values = {
            "HERMES_DATA_DIR": str(self.data),
            "HERMES_WORKSPACE_DIR": str(self.workspace),
            "HERMES_AUTH_ENV_FILE": str(self.auth),
            "HERMES_PUBLIC_URL": "https://council.example.test",
            "HERMES_UID": "10000",
            "HERMES_GID": "10000",
            "HERMES_HTTP_PORT": "9119",
            "COUNCIL_HERMES_IMAGE": "agent-council-hermes:previous",
        }
        self.save()
        (self.data / "history.txt").write_text("original history")
        (self.data / ".env").write_text("TELEGRAM_BOT_TOKEN=fake-private-bot-token\n")
        (self.data / ".env").chmod(0o600)
        (self.workspace / "report.txt").write_text("original report")

    def save(self):
        self.env.write_text("".join(f"{key}={value}\n" for key, value in self.values.items()))
        self.env.chmod(0o600)

    def settings(self):
        return Settings.load(self.repository, self.env, self.backups)


class FakeRuntime:
    def __init__(self, settings):
        self.settings = settings
        self.events = []
        self.running = True
        self.fail_candidate = False
        self.reject_candidate_copy = False

    def image(self):
        return IMAGE, {"org.opencontainers.image.revision": "c" * 40}

    def running_services(self):
        return ["hermes"]

    def compose_source(self):
        return self.settings.env_file.with_name("active-compose.yaml")

    def activate_source(self, source):
        self.compose_source().write_bytes(source.read_bytes())

    def inspect(self):
        return {"State": {"Running": self.running, "Health": {"Status": "healthy"}}}

    def require_idle(self):
        self.events.append("idle")

    def stop(self):
        self.events.append("stop")
        self.running = False

    @contextmanager
    def frozen(self):
        self.events.append("pause")
        try:
            yield None
        finally:
            self.events.append("unpause")

    def start(self):
        self.events.append("start")
        self.running = True
        if read_settings(self.settings.env_file)["COUNCIL_HERMES_IMAGE"].endswith(REVISION):
            (self.settings.data_dir / "history.txt").write_text("candidate migrated state")

    def ready(self):
        self.events.append("ready")
        image = read_settings(self.settings.env_file)["COUNCIL_HERMES_IMAGE"]
        if self.fail_candidate and image.endswith(REVISION):
            raise OperationError("Candidate failed readiness.")

    def check_protected(self):
        self.events.append("protected")

    def revision(self):
        return REVISION

    def run(self, arguments, **kwargs):
        self.events.append(tuple(arguments))
        if arguments[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Size": 1024}])
        if arguments[:3] == ["docker", "image", "save"]:
            Path(arguments[arguments.index("--output") + 1]).write_bytes(b"fake image")
        if arguments[:2] == ["docker", "run"] and self.reject_candidate_copy:
            if "agent-council-hermes:" + REVISION in arguments:
                raise OperationError("Candidate cannot open copied state.")
        return ""


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Fixture(self.temporary.name)
        # Unit tests own their temporary files; production takes explicit non-root UID/GID.
        self.identity = patch(
            "council_ops.lifecycle.identity", return_value=(os.getuid(), os.getgid())
        )
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def test_rejects_unsafe_settings_before_any_service_operation(self):
        cases = [
            ("HERMES_DATA_DIR", str(self.fixture.repository / "data")),
            ("HERMES_DATA_DIR", str(self.fixture.workspace / "nested")),
            ("HERMES_DATA_DIR", "relative-data"),
            ("HERMES_UID", "0"),
            ("HERMES_PUBLIC_URL", "https://user:password@example.test"),
            ("HERMES_PUBLIC_URL", "http://example.test"),
            ("HERMES_HTTP_PORT", "443"),
            ("COUNCIL_HERMES_IMAGE", "$UNTRUSTED"),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                old = self.fixture.values[key]
                self.fixture.values[key] = value
                self.fixture.save()
                with self.assertRaises(OperationError):
                    self.fixture.settings()
                self.fixture.values[key] = old
        self.fixture.save()
        self.fixture.env.write_text(self.fixture.env.read_text() + "HERMES_UID=20000\n")
        with self.assertRaises(OperationError):
            self.fixture.settings()

    def test_private_consistent_snapshot_preserves_history_and_does_not_follow_links(self):
        outside = self.fixture.root / "outside.txt"
        outside.write_text("outside-sensitive-content")
        (self.fixture.workspace / "link").symlink_to(outside)
        runtime = FakeRuntime(self.fixture.settings())
        backup = snapshot(runtime)
        self.assertEqual(runtime.events[:3], ["idle", "pause", "unpause"])
        self.assertTrue(runtime.running)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertEqual((backup / "auth.env").stat().st_mode & 0o777, 0o600)
        self.assertTrue((backup / "workspace/link").is_symlink())
        _, manifest = load_manifest(backup)
        self.assertNotIn("outside-sensitive-content", json.dumps(manifest))
        self.assertEqual((backup / "hermes/history.txt").read_text(), "original history")
        (backup / "hermes/history.txt").write_text("tampered")
        with self.assertRaises(OperationError):
            load_manifest(backup)

    def test_failed_snapshot_restarts_the_previous_service(self):
        runtime = FakeRuntime(self.fixture.settings())
        with patch("council_ops.snapshots.copy_state", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                snapshot(runtime, restart=False)
        self.assertTrue(runtime.running)
        self.assertEqual(runtime.events[-3:], ["start", "ready", "protected"])
        self.assertFalse(list(self.fixture.backups.glob("*/manifest.json")))

    def test_failed_routine_snapshot_thaws_without_restarting_or_publishing_a_manifest(self):
        runtime = FakeRuntime(self.fixture.settings())
        with patch("council_ops.snapshots.copy_state", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                snapshot(runtime, portable=True)
        self.assertTrue(runtime.running)
        self.assertEqual(runtime.events, ["idle", "pause", "unpause"])
        self.assertFalse(list(self.fixture.backups.glob("*/manifest.json")))

    def test_freeze_watchdog_is_armed_before_pausing_and_kept_after_thaw_failure(self):
        runtime = Runtime(self.fixture.settings())
        calls = []
        hermes, worker = "a" * 64, "b" * 64

        def run(arguments, **_kwargs):
            calls.append(arguments)
            if arguments[:2] == ["docker", "inspect"]:
                return json.dumps([{"State": {"Running": True}}])
            if arguments == ["docker", "unpause", worker]:
                raise OperationError("Fixture thaw failure")
            return ""

        with (
            patch.object(runtime, "container", return_value=hermes),
            patch.object(runtime, "companion", return_value=worker),
            patch.object(runtime, "run", side_effect=run),
        ):
            with self.assertRaisesRegex(OperationError, "watchdog remains armed"):
                with runtime.frozen():
                    pass
        armed = next(i for i, command in enumerate(calls) if command[0] == "systemd-run")
        pause = next(i for i, command in enumerate(calls) if command[:2] == ["docker", "pause"])
        self.assertLess(armed, pause)
        self.assertIn(["docker", "unpause", hermes], calls)
        self.assertFalse(any(command[:2] == ["systemctl", "stop"] for command in calls))

    def test_watchdog_setup_failure_never_freezes_a_container(self):
        runtime = Runtime(self.fixture.settings())
        with (
            patch.object(runtime, "container", return_value="a" * 64),
            patch.object(runtime, "companion", return_value=None),
            patch.object(runtime, "run", side_effect=OperationError("No system manager")) as run,
        ):
            with self.assertRaises(OperationError):
                with runtime.frozen():
                    self.fail("Must not freeze without an independent watchdog")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], "systemd-run")

    def test_snapshot_freezes_all_linear_writers_and_thaws_them_in_reverse_order(self):
        runtime = Runtime(self.fixture.settings())
        identifiers = [letter * 64 for letter in "abcde"]
        calls = []

        def run(arguments, **_kwargs):
            calls.append(arguments)
            if arguments[:2] == ["docker", "inspect"]:
                return json.dumps([{"State": {"Running": True}}])
            return ""

        with (
            patch.object(runtime, "container", return_value=identifiers[0]),
            patch.object(runtime, "companion", return_value=identifiers[1]),
            patch.object(
                runtime,
                "linear_companions",
                return_value=[
                    (name, cid, True)
                    for name, cid in zip(
                        ("linear-adapter", "linear-executor", "dexter-executor"),
                        identifiers[2:],
                        strict=True,
                    )
                ],
            ),
            patch.object(runtime, "run", side_effect=run),
        ):
            with runtime.frozen():
                pass
        self.assertEqual([row[-1] for row in calls if row[:2] == ["docker", "pause"]], identifiers)
        self.assertEqual(
            [row[-1] for row in calls if row[:2] == ["docker", "unpause"]], identifiers[::-1]
        )
        self.assertEqual(next(row[-5:] for row in calls if row[0] == "systemd-run"), identifiers)

    def test_partial_freeze_failure_thaws_the_first_container(self):
        runtime = Runtime(self.fixture.settings())
        calls = []
        hermes, worker = "a" * 64, "b" * 64

        def run(arguments, **_kwargs):
            calls.append(arguments)
            if arguments[:2] == ["docker", "inspect"]:
                return json.dumps([{"State": {"Running": True}}])
            if arguments == ["docker", "pause", worker]:
                raise OperationError("Fixture pause failure")
            return ""

        with (
            patch.object(runtime, "container", return_value=hermes),
            patch.object(runtime, "companion", return_value=worker),
            patch.object(runtime, "run", side_effect=run),
        ):
            with self.assertRaises(OperationError):
                with runtime.frozen():
                    self.fail("Partial freeze must not copy state")
        self.assertIn(["docker", "unpause", hermes], calls)
        self.assertTrue(any(command[:2] == ["systemctl", "stop"] for command in calls))

    def test_cloud_checkpoint_excludes_software_but_keeps_credentials_skills_and_workspace(self):
        for relative in (
            "tools/node",
            "cache/uv",
            "installs/runtime",
            "logs/log",
            "backups/old",
            "profiles/dexter/cache/tmp",
            ".council/secretary/tools/python",
        ):
            path = self.fixture.data / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("rebuildable or disposable")
        for relative in (
            "skills/custom/tools/script.py",
            "profiles/dexter/memories/notes.md",
            "mcp-tokens/notion.json",
            ".council/secretary/auth.json",
        ):
            path = self.fixture.data / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("must survive")
        (self.fixture.workspace / "cache").mkdir()
        (self.fixture.workspace / "cache/report.txt").write_text("user output, not a system cache")
        runtime = FakeRuntime(self.fixture.settings())
        backup = snapshot(runtime, portable=True)
        _, manifest = load_manifest(backup)
        self.assertEqual(manifest["kind"], "portable-data")
        self.assertNotIn("image.tar", manifest["files"])
        for relative in (
            "tools",
            "cache",
            "installs",
            "logs",
            "backups",
            "profiles/dexter/cache",
            ".council/secretary/tools",
        ):
            self.assertFalse((backup / "hermes" / relative).exists())
        for relative in (
            "skills/custom/tools/script.py",
            "profiles/dexter/memories/notes.md",
            "mcp-tokens/notion.json",
        ):
            self.assertEqual((backup / "hermes" / relative).read_text(), "must survive")
        self.assertEqual(
            (backup / "hermes/.council/secretary/auth.json").read_text(), "must survive"
        )
        self.assertTrue((backup / "hermes/.env").is_file())
        self.assertEqual(
            (backup / "workspace/cache/report.txt").read_text(), "user output, not a system cache"
        )
        self.assertTrue((self.fixture.data / "tools/node").is_file())

    def test_copied_profile_cannot_overwrite_an_existing_or_symlinked_destination(self):
        target = self.fixture.root / "destination"
        target.symlink_to(self.fixture.workspace, target_is_directory=True)
        with self.assertRaises(OperationError):
            copy_state(self.fixture.data, target)
        self.assertEqual((self.fixture.workspace / "report.txt").read_text(), "original report")

    def test_candidate_copy_is_isolated_and_secrets_are_not_command_arguments(self):
        runtime = FakeRuntime(self.fixture.settings())
        backup = snapshot(runtime)
        smoke(runtime, backup)
        command = next(
            event
            for event in runtime.events
            if isinstance(event, tuple) and event[:2] == ("docker", "run")
        )
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertIn("ALL", command)
        self.assertNotIn("fake-secret-not-for-arguments", " ".join(command))
        self.assertNotIn("fake-private-bot-token", " ".join(command))

    def test_failed_candidate_migration_restores_image_and_data_together(self):
        runtime = FakeRuntime(self.fixture.settings())
        runtime.fail_candidate = True
        original_auth = self.fixture.auth.read_bytes()
        with self.assertRaisesRegex(OperationError, "previous image and state were restored"):
            deploy(runtime)
        self.assertTrue(runtime.running)
        self.assertEqual((self.fixture.data / "history.txt").read_text(), "original history")
        self.assertEqual(self.fixture.auth.read_bytes(), original_auth)
        self.assertEqual(read_settings(self.fixture.env)["COUNCIL_HERMES_IMAGE"], IMAGE)
        self.assertEqual(runtime.compose_source().read_text(), "previous Compose source\n")
        retained = list(self.fixture.data.parent.glob("hermes.before-restore-*/history.txt"))
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_text(), "candidate migrated state")
        build = next(
            index
            for index, event in enumerate(runtime.events)
            if isinstance(event, tuple) and event[:2] == ("docker", "build")
        )
        self.assertLess(build, runtime.events.index("stop"))

    def test_failed_copy_check_never_switches_the_live_image(self):
        runtime = FakeRuntime(self.fixture.settings())
        runtime.reject_candidate_copy = True
        original = self.fixture.env.read_bytes()
        with self.assertRaisesRegex(OperationError, "restarted unchanged"):
            deploy(runtime)
        self.assertTrue(runtime.running)
        self.assertEqual(self.fixture.env.read_bytes(), original)
        self.assertEqual((self.fixture.data / "history.txt").read_text(), "original history")

    def test_restore_retains_the_previous_live_state(self):
        runtime = FakeRuntime(self.fixture.settings())
        backup = snapshot(runtime)
        (self.fixture.data / "history.txt").write_text("newer live data")
        retained = restore(runtime, backup)
        self.assertEqual((self.fixture.data / "history.txt").read_text(), "original history")
        self.assertEqual((Path(retained["hermes"]) / "history.txt").read_text(), "newer live data")

    def test_secretary_key_and_service_selection_restore_with_the_profile(self):
        runtime = FakeRuntime(self.fixture.settings())
        runtime.running_services = lambda: ["hermes", "secretary"]
        private_key = runtime.settings.secretary_file
        private_key.write_text("COUNCIL_SECRETARY_KEY=" + "a" * 64 + "\n")
        private_key.chmod(0o600)
        backup = snapshot(runtime)
        private_key.write_text("COUNCIL_SECRETARY_KEY=" + "b" * 64 + "\n")
        restore(runtime, backup)
        self.assertIn("a" * 64, private_key.read_text())
        self.assertEqual(read_settings(self.fixture.env)["COUNCIL_SECRETARY_ENABLED"], "true")

    def test_restore_without_a_secretary_does_not_inherit_newer_machine_credentials(self):
        runtime = FakeRuntime(self.fixture.settings())
        backup = snapshot(runtime)
        private_key = runtime.settings.secretary_file
        private_key.write_text("COUNCIL_SECRETARY_KEY=fixture-newer-key\n")
        restore(runtime, backup)
        self.assertFalse(private_key.exists())
        self.assertEqual(read_settings(self.fixture.env)["COUNCIL_SECRETARY_ENABLED"], "false")

    def test_compose_cannot_dump_secrets_or_inherit_a_foreign_volume(self):
        runtime = Runtime(self.fixture.settings())
        with self.assertRaises(OperationError):
            runtime.compose("config")
        with patch.object(runtime, "run", return_value="") as run:
            with patch.dict(os.environ, {"HERMES_DATA_DIR": "/unrelated-application"}):
                runtime.compose("config", "--quiet")
        self.assertNotIn("HERMES_DATA_DIR", run.call_args.kwargs["environment"])

    def test_retention_does_not_delete_manual_unverified_or_foreign_backups(self):
        root = self.fixture.backups
        root.mkdir(parents=True)
        for name, owner, verified in [
            ("backup-01", "agent-council", True),
            ("backup-02", "agent-council", True),
            ("backup-03", "agent-council", False),
            ("backup-04", "other-application", True),
        ]:
            folder = root / name
            folder.mkdir()
            (folder / "manifest.json").write_text(
                json.dumps(
                    {
                        "application": owner,
                        "schema": 1,
                        "offsite_verified": "a" * 64 if verified else None,
                    }
                )
            )
        prune_local(root, keep=1)
        self.assertEqual(
            sorted(p.name for p in root.iterdir()), ["backup-02", "backup-03", "backup-04"]
        )

    def test_r2_configuration_is_scoped_private_and_not_passed_in_arguments(self):
        env = self.fixture.config / "backup.env"
        env.write_text(
            "RESTIC_REPOSITORY=s3:https://"
            + "a" * 32
            + ".r2.cloudflarestorage.com/council-backups/production\nAWS_ACCESS_KEY_ID=fake-access-key-for-tests\nAWS_SECRET_ACCESS_KEY=fake-secret-key-for-tests\nAWS_DEFAULT_REGION=auto\n"
        )
        env.chmod(0o600)
        password = self.fixture.config / "backup-password"
        password.write_text("fake-long-passphrase-for-test-only")
        password.chmod(0o600)
        runtime = FakeRuntime(self.fixture.settings())
        remote = Restic(runtime)
        remote.run(["check"], source=self.fixture.workspace, target=self.fixture.workspace)
        command = runtime.events[-1]
        self.assertNotIn("fake-secret", " ".join(command))
        env.chmod(0o644)
        with self.assertRaises(OperationError):
            Restic(runtime)
        env.chmod(0o600)
        env.write_text(env.read_text().replace(".r2.cloudflarestorage.com", ".attacker.example"))
        with self.assertRaises(OperationError):
            Restic(runtime)
