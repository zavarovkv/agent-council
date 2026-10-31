"""Collector receipts cannot reuse stale evidence; recovery preserves user grants."""

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from test_operations import FakeRuntime, Fixture

from council_ops.config import OperationError
from council_ops.lifecycle import restore
from council_ops.snapshots import load_manifest, snapshot
from council_ops.telegram_reader import collect, workspace
from telegram_monitor.evidence import ZONE, daily_window


def reader_env(letter="a"):
    return (
        "\n".join(
            (
                "COUNCIL_TG_READER_API_ID=1",
                "COUNCIL_TG_READER_API_HASH=" + letter * 32,
                "COUNCIL_TG_READER_SESSION=1" + letter * 200,
                "COUNCIL_TG_READER_ACCOUNT_ID=10",
                "COUNCIL_TG_READER_PEER_ID=20",
                "COUNCIL_TG_READER_SOURCE=fixture_source",
            )
        )
        + "\n"
    )


class ReaderOperationsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = Fixture(temporary.name)
        self.runtime = FakeRuntime(self.fixture.settings())
        self.settings = self.runtime.settings
        self.settings.telegram_reader_file.write_text(reader_env())
        self.settings.telegram_reader_file.chmod(0o600)
        for name in ("council_ops.telegram_reader.identity", "council_ops.lifecycle.identity"):
            identity = patch(name, return_value=(os.getuid(), os.getgid()))
            identity.start()
            self.addCleanup(identity.stop)

    def test_expired_artifact_cannot_be_accepted_after_a_failed_collection(self):
        root = workspace(self.runtime)
        start, end = daily_window(datetime.now(UTC))
        artifact = root / "raw" / (end.astimezone(ZONE).date().isoformat() + ".json")
        value = {
            "schema": 1,
            "source": "fixture_source",
            "peer_id": 20,
            "window_end": end.isoformat(),
            "generated_at": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
            "status": "complete",
        }
        artifact.write_text(json.dumps(value))
        self.runtime.compose = lambda *args, **kwargs: (_ for _ in ()).throw(
            OperationError("No fresh receipt")
        )
        with self.assertRaises(OperationError):
            collect(self.runtime)
        value.update(
            status="unavailable",
            coverage="unknown",
            reason="source_unavailable",
            generated_at=datetime.now(UTC).isoformat(),
        )
        artifact.write_text(json.dumps(value))
        self.assertEqual(collect(self.runtime)["status"], "unavailable")

    def test_portable_backup_includes_the_private_session_but_no_image(self):
        backup = snapshot(self.runtime, portable=True)
        _, manifest = load_manifest(backup)
        self.assertIn("telegram-reader.env", manifest["files"])
        self.assertNotIn("image.tar", manifest["files"])
        self.assertEqual((backup / "telegram-reader.env").stat().st_mode & 0o777, 0o600)

    def test_session_path_cannot_be_relative_shared_or_inside_a_runtime_mount(self):
        for path in (
            "relative.env",
            str(self.fixture.repository / "reader.env"),
            str(self.fixture.data / "reader.env"),
            str(self.fixture.auth),
            str(self.fixture.env),
            str(self.settings.secretary_file),
        ):
            self.fixture.values["COUNCIL_TG_READER_ENV_FILE"] = path
            self.fixture.save()
            with self.subTest(path=path), self.assertRaises(OperationError):
                self.fixture.settings()

    def test_failed_restore_retains_the_current_reader_grant(self):
        backup = snapshot(self.runtime)
        self.settings.telegram_reader_file.write_text(reader_env("b"))
        with (
            patch("council_ops.instructions.verify_capabilities"),
            patch.object(
                self.runtime, "ready", side_effect=[OperationError("Candidate unready"), None]
            ),
        ):
            with self.assertRaises(OperationError):
                restore(self.runtime, backup)
        self.assertEqual(self.settings.telegram_reader_file.read_text(), reader_env("b"))
