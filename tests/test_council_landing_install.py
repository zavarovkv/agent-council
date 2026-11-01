"""Exercise the offline static landing installer without touching host paths."""

import hashlib
import importlib.util
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/install-council-landing.py"
spec = importlib.util.spec_from_file_location("council_landing_installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def archive(path, entries):
    with tarfile.open(path, "w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LandingInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dest = self.root / "www"
        self.dest.mkdir()
        self.patches = [
            patch.object(installer, "DEST", self.dest),
            patch.object(installer, "LINK", self.dest / "agent-council-landing"),
            patch.object(installer, "RELEASES", self.dest / "agent-council-landing-releases"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def invoke(self, path, digest):
        with patch.object(sys, "argv", ["installer", str(path), "--sha256", digest]):
            installer.main()

    def test_valid_artifact_is_installed_atomically_and_readable(self):
        path = self.root / "valid.tar.gz"
        digest = archive(
            path,
            {
                "index.html": b"<html>https://council.zavarov.com/</html>",
                "en/index.html": b"<html>https://council.zavarov.com/en/</html>",
                "council/avatars/qui-gon.png": b"png fixture",
            },
        )
        self.invoke(path, digest)
        self.assertIn(b"council.zavarov.com", (installer.LINK / "index.html").read_bytes())
        self.assertIn(b"council.zavarov.com", (installer.LINK / "en/index.html").read_bytes())
        self.assertTrue((installer.LINK.resolve().stat().st_mode & 0o111) != 0)
        self.invoke(path, digest)  # idempotent install

    def test_rejects_bad_checksum_without_switching(self):
        path = self.root / "valid.tar.gz"
        archive(path, {"index.html": b"content"})
        with self.assertRaises(SystemExit):
            self.invoke(path, "0" * 64)
        self.assertFalse(installer.LINK.exists())

    def test_rejects_traversal_and_absolute_paths(self):
        for name in ("../escape", "/tmp/escape"):
            with self.subTest(name=name):
                path = self.root / "invalid.tar.gz"
                digest = archive(path, {name: b"escape"})
                with self.assertRaises(SystemExit):
                    self.invoke(path, digest)
                self.assertFalse(installer.LINK.exists())


if __name__ == "__main__":
    unittest.main()
