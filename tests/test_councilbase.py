import os
import tempfile
import unittest
from pathlib import Path

from council_ops.councilbase import (
    ContextValidationError,
    read_snapshot,
    validate_document,
    write_snapshot,
    retrieve_topic,
)

DOCUMENT = "1unboDfPqJ0U86nyaj9tuNM_CwSK17CKF"


class CouncilBaseSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.directory.chmod(0o700)

    def test_roundtrip(self):
        data = "# Контекст\nПример".encode()
        digest = write_snapshot(self.directory, DOCUMENT, data, "text/markdown")
        saved = read_snapshot(self.directory, DOCUMENT)
        self.assertEqual(saved["sha256"], digest)
        self.assertEqual(saved["text"], data.decode())
        self.assertTrue(saved["synced_at"])
        self.assertEqual((self.directory / "councilbase-context.json").stat().st_mode & 0o077, 0)

    def test_wrong_document_cannot_read(self):
        write_snapshot(self.directory, DOCUMENT, b"# Hello", "text/markdown")
        with self.assertRaises(ContextValidationError):
            read_snapshot(self.directory, "anotherDocumentId01234567890")

    def test_rejects_invalid_payloads(self):
        for payload, mime in [
            (b"", "text/markdown"),
            (b"x" * (256 * 1024 + 1), "text/markdown"),
            (b"\x00", "text/markdown"),
            (b"\xff", "text/markdown"),
            (b"hello", "text/html"),
        ]:
            with self.subTest(mime=mime, length=len(payload)):
                with self.assertRaises(ContextValidationError):
                    validate_document(DOCUMENT, payload, mime)

    def test_rejects_world_readable_directory(self):
        self.directory.chmod(0o755)
        with self.assertRaises(ContextValidationError):
            write_snapshot(self.directory, DOCUMENT, b"hello", "text/plain")

    def test_rejects_modified_snapshot(self):
        write_snapshot(self.directory, DOCUMENT, b"hello", "text/plain")
        path = self.directory / "councilbase-context.json"
        path.write_text(path.read_text().replace("hello", "other"))
        os.chmod(path, 0o600)
        with self.assertRaises(ContextValidationError):
            read_snapshot(self.directory, DOCUMENT)

    def test_topic_retrieval_is_bounded_and_source_labeled(self):
        write_snapshot(
            self.directory,
            DOCUMENT,
            "# Profile\\nName\\n## Hermes\\nHermes has a private agent.\\n"
            "## Finance\\nBank records must stay private.".encode(),
            "text/markdown",
        )
        snapshot = read_snapshot(self.directory, DOCUMENT)
        result = retrieve_topic(snapshot, "Hermes", max_chars=200)
        self.assertTrue(result["source_is_untrusted"])
        self.assertEqual(result["excerpts"][0]["heading"], "Hermes")
        self.assertEqual(result["document_id"], DOCUMENT)
        self.assertLessEqual(sum(len(x["text"]) for x in result["excerpts"]), 200)

    def test_retrieval_rejects_unbounded_query(self):
        snapshot = {"text": "# Context\\nData", "document_id": DOCUMENT,
                    "synced_at": "now", "sha256": "abc"}
        with self.assertRaises(ContextValidationError):
            retrieve_topic(snapshot, "x" * 201)

    def test_rejects_symlink_target(self):
        outside = self.directory / "outside"
        outside.write_text("not private")
        (self.directory / "councilbase-context.json").symlink_to(outside)
        with self.assertRaises(ContextValidationError):
            write_snapshot(self.directory, DOCUMENT, b"hello", "text/plain")


if __name__ == "__main__":
    unittest.main()
