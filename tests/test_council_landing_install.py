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
        self.root = Path(self.tmp.name).resolve()
        self.dest = self.root / "www"
        self.dest.mkdir()
        self.patches = [
            patch.object(installer, "LOCK", self.root / "operation.lock"),
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

    def test_bad_candidate_keeps_previous_release(self):
        first = self.root / "first.tar.gz"
        digest = archive(
            first,
            {
                "index.html": b"<html>https://council.zavarov.com/</html>",
                "en/index.html": b"<html>https://council.zavarov.com/en/</html>",
                "council/avatars/qui-gon.png": b"png fixture",
            },
        )
        self.invoke(first, digest)
        previous = installer.LINK.resolve()
        second = self.root / "bad.tar.gz"
        digest = archive(
            second,
            {
                "index.html": b"invalid page",
                "en/index.html": b"<html>https://council.zavarov.com/en/</html>",
                "council/avatars/qui-gon.png": b"png fixture",
            },
        )
        with self.assertRaises(SystemExit):
            self.invoke(second, digest)
        self.assertEqual(installer.LINK.resolve(), previous)

    def test_post_switch_failure_restores_previous_pointer(self):
        first = self.root / "first.tar.gz"
        digest = archive(
            first,
            {
                "index.html": b"<html>https://council.zavarov.com/</html>",
                "en/index.html": b"<html>https://council.zavarov.com/en/</html>",
                "council/avatars/qui-gon.png": b"png fixture",
            },
        )
        self.invoke(first, digest)
        previous = installer.LINK.resolve()
        second = self.root / "second.tar.gz"
        digest = archive(
            second,
            {
                "index.html": b"<html>https://council.zavarov.com/changed</html>",
                "en/index.html": b"<html>https://council.zavarov.com/en/</html>",
                "council/avatars/qui-gon.png": b"png fixture",
            },
        )
        original_replace = installer.os.replace
        calls = 0

        def fail_verification_once(source, destination):
            nonlocal calls
            calls += 1
            result = original_replace(source, destination)
            if calls == 1:
                # Simulate a broken new target immediately after activation.
                (installer.LINK / "en/index.html").unlink()
            return result

        with patch.object(installer.os, "replace", side_effect=fail_verification_once):
            with self.assertRaises(SystemExit):
                self.invoke(second, digest)
        self.assertEqual(installer.LINK.resolve(), previous)
        self.assertTrue((installer.LINK / "en/index.html").is_file())

    def test_failed_http_smoke_restores_previous_release(self):
        first = self.root / "first.tar.gz"
        digest = archive(
            first,
            {
                "index.html": b"<html>https://council.zavarov.com/</html>",
                "en/index.html": b"<html>https://council.zavarov.com/en/</html>",
                "council/avatars/qui-gon.png": b"png fixture",
            },
        )
        self.invoke(first, digest)
        previous = installer.LINK.resolve()
        second = self.root / "second.tar.gz"
        digest = archive(
            second,
            {
                "index.html": b"<html>https://council.zavarov.com/new</html>",
                "en/index.html": b"<html>https://council.zavarov.com/en/</html>",
                "council/avatars/qui-gon.png": b"png fixture",
            },
        )
        with (
            patch.object(
                sys,
                "argv",
                [
                    "installer",
                    str(second),
                    "--sha256",
                    digest,
                    "--verify-url",
                    "https://council.zavarov.com",
                ],
            ),
            patch.object(
                installer.subprocess,
                "run",
                side_effect=installer.subprocess.CalledProcessError(1, "smoke"),
            ),
        ):
            with self.assertRaises(installer.subprocess.CalledProcessError):
                installer.main()
        self.assertEqual(installer.LINK.resolve(), previous)

    def test_uses_checked_bytes_when_sender_replaces_archive(self):
        path = self.root / "valid.tar.gz"
        entries = {
            "index.html": b"<html>https://council.zavarov.com/</html>",
            "en/index.html": b"<html>https://council.zavarov.com/en/</html>",
            "council/avatars/qui-gon.png": b"png fixture",
        }
        digest = archive(path, entries)
        original_gzip = installer.gzip.GzipFile

        def replace_after_hash(*args, **kwargs):
            path.write_bytes(b"replacement after checksum")
            return original_gzip(*args, **kwargs)

        with patch.object(installer.gzip, "GzipFile", side_effect=replace_after_hash):
            self.invoke(path, digest)
        self.assertEqual((installer.LINK / "index.html").read_bytes(), entries["index.html"])
        self.assertEqual(installer.LINK.resolve().name, digest)

    def test_rejects_changed_cached_release_and_operations_lock_symlink(self):
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
        (installer.LINK / "index.html").write_bytes(b"tampered")
        with self.assertRaises(SystemExit):
            self.invoke(path, digest)
        installer.LOCK.unlink()
        target = self.root / "unrelated"
        target.write_text("preserved")
        installer.LOCK.symlink_to(target)
        with self.assertRaises(SystemExit):
            self.invoke(path, digest)
        self.assertEqual(target.read_text(), "preserved")

    def test_rejects_duplicate_entries_links_and_unserved_paths(self):
        for names, kind in (
            (["index.html", "index.html"], tarfile.REGTYPE),
            (["council/link"], tarfile.SYMTYPE),
            (["auth/token"], tarfile.REGTYPE),
        ):
            with self.subTest(names=names, kind=kind):
                path = self.root / "invalid.tar.gz"
                with tarfile.open(path, "w:gz") as tar:
                    for name in names:
                        entry = tarfile.TarInfo(name)
                        entry.type = kind
                        entry.linkname = "/outside"
                        tar.addfile(entry, io.BytesIO(b""))
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                with self.assertRaises(SystemExit):
                    self.invoke(path, digest)
                self.assertFalse(installer.LINK.exists())

    def test_rejects_oversized_compressed_and_expanded_archives(self):
        path = self.root / "large.tar.gz"
        digest = archive(path, {"index.html": b"x" * 4096})
        for limit in ("MAX_ARCHIVE", "MAX_TAR"):
            with self.subTest(limit=limit), patch.object(installer, limit, 16):
                with self.assertRaises(SystemExit):
                    self.invoke(path, digest)
                self.assertFalse(installer.LINK.exists())

    def test_rejects_bad_checksum_without_switching(self):
        path = self.root / "valid.tar.gz"
        archive(path, {"index.html": b"content"})
        with self.assertRaises(SystemExit):
            self.invoke(path, "0" * 64)
        self.assertFalse(installer.LINK.exists())

    def test_busy_operation_lock_is_retryable_without_switching(self):
        with (
            patch.object(sys, "argv", ["installer", "unused", "--sha256", "a" * 64]),
            patch.object(installer, "read_artifact", return_value={}),
            installer.operation_lock(),
            self.assertRaises(SystemExit) as error,
        ):
            installer.main()
        self.assertEqual(error.exception.code, 75)
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
