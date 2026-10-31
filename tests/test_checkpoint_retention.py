"""Finite automatic rollback history without deleting operator-created exports."""

import json
import tempfile
import unittest

from test_operations import FakeRuntime, Fixture

from council_ops.config import OperationError
from council_ops.snapshots import prune_automatic_checkpoints, snapshot


class CheckpointRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = Fixture(temporary.name)
        self.runtime = FakeRuntime(self.fixture.settings())

    def test_repeated_operations_keep_two_automatic_points_and_preserve_other_state(self):
        manual = snapshot(self.runtime)
        legacy = snapshot(self.runtime)
        marker = legacy / "manifest.json"
        value = json.loads(marker.read_text())
        del value["automatic"]
        marker.write_text(json.dumps(value))
        pinned = snapshot(self.runtime, automatic=True)
        marker = pinned / "manifest.json"
        value = json.loads(marker.read_text())
        value["pinned"] = True
        marker.write_text(json.dumps(value))
        points = [snapshot(self.runtime, automatic=True) for _ in range(5)]
        self.assertTrue(all(not point.exists() for point in points[:-2]))
        self.assertTrue(all(point.exists() for point in points[-2:]))
        self.assertTrue(all(point.exists() for point in (manual, legacy, pinned)))
        self.assertEqual((self.fixture.data / "history.txt").read_text(), "original history")
        self.assertTrue(self.runtime.running)

    def test_invalid_retained_copy_or_foreign_protection_prevents_any_deletion(self):
        points = [snapshot(self.runtime) for _ in range(3)]
        for point in points:
            marker = point / "manifest.json"
            value = json.loads(marker.read_text())
            value["automatic"] = True
            marker.write_text(json.dumps(value))
        root = self.fixture.backups
        (points[-1] / "hermes/history.txt").write_text("corrupted retained copy")
        with self.assertRaises(OperationError):
            prune_automatic_checkpoints(root, protected=points[-1])
        self.assertTrue(all(point.exists() for point in points))
        with self.assertRaises(OperationError):
            prune_automatic_checkpoints(root, protected=self.fixture.data)
        self.assertTrue(all(point.exists() for point in points))
