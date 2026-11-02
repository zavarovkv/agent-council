import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_workspace import config

from council_workspace.index import MARKER, Navigation
from council_workspace.service import Workspace


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        config(self.home)
        self.navigation = Navigation(
            SimpleNamespace(home=self.home, store=Workspace(self.home).store, lock=threading.Lock())
        )

    def test_refresh_publishes_two_indices_without_recreating_them_and_preserves_on_failure(self):
        from council_recurring.client import atomic_json
        from council_workspace.notion import revision

        root, child, nav = "1" * 32, "2" * 32, "8" * 32
        page_rows = {
            root: {
                "id": root,
                "parent": None,
                "content": f'<page url="https://notion.so/{child}">Project</page>',
            },
            child: {"id": child, "parent": root, "content": "Source"},
        }
        drive_rows = {}
        mutations = []
        pages = SimpleNamespace(root=root, fetch=lambda identity: page_rows[identity])
        files = SimpleNamespace(root="folder-fixture")

        def drive_read(args):
            if args["action"] == "drive_read":
                import base64

                return {
                    "base64": base64.b64encode(drive_rows[args["id"]]["content"].encode()).decode()
                }
            return {"files": [r for r in drive_rows.values()]}

        files.read = drive_read

        def prepare(provider, args):
            def perform():
                mutations.append(args["action"])
                if provider == "notion":
                    identity = args.get("id", nav)
                    page_rows[identity] = {
                        "id": identity,
                        "parent": root,
                        "content": args["content"],
                    }
                    if args["action"] == "notion_create":
                        page_rows[root]["content"] += (
                            f'<page url="https://notion.so/{identity}">Навигация</page>'
                        )
                    return {
                        "id": identity,
                        "url": "https://www.notion.so/" + identity,
                        "revision": revision(args["content"]),
                        "verified": True,
                    }
                identity = args.get("id", "index-fixture")
                drive_rows[identity] = {
                    "id": identity,
                    "name": "INDEX.md",
                    "version": str(len(mutations)),
                    "mimeType": "text/markdown",
                    "content": args["content"],
                    "webViewLink": "https://drive.google.com/file/d/" + identity,
                }
                return {**drive_rows[identity], "verified": True}

            return perform

        pages.prepare_write = lambda args: prepare("notion", args)
        files.prepare_write = lambda args: prepare("drive", args)
        self.navigation.workspace.notion_call = Mock()
        self.navigation.workspace.drive_factory = Mock()
        atomic_json(
            self.home / ".council/workspace/index-config.json",
            {
                "schema": 1,
                "enabled": True,
                "notion_root": root,
                "drive_root": "folder-fixture",
                "job": "fixture",
            },
        )
        with (
            patch("council_workspace.index.Pages", return_value=pages),
            patch("council_workspace.index.Files", return_value=files),
        ):
            self.navigation.refresh()
            self.navigation.refresh()
            self.assertEqual(mutations.count("notion_create"), 1)
            self.assertEqual(mutations.count("drive_create"), 1)
            self.assertEqual(self.navigation.status()["notion_count"], 1)
            self.assertEqual(self.navigation.status()["drive_count"], 0)
            self.assertIn("Project", drive_rows["index-fixture"]["content"])
            saved = list(mutations)
            files.read = Mock(side_effect=OSError("offline"))
            self.navigation.run()
            self.assertEqual(saved, mutations)
            self.assertTrue(self.navigation.status()["error"])

    def test_drive_rebuild_handles_pagination_rename_move_and_deletion(self):
        root, folder, leaf = "root-folder-1", "sub-folder-1", "file-1"
        records = {
            (root, None): {
                "files": [
                    {
                        "id": folder,
                        "name": "Project",
                        "mimeType": "application/vnd.google-apps.folder",
                    }
                ],
                "nextPageToken": "next",
            },
            (root, "next"): {
                "files": [{"id": "index-1", "name": "INDEX.md", "mimeType": "text/markdown"}]
            },
            (folder, None): {"files": [{"id": leaf, "name": "Draft.docx", "mimeType": "document"}]},
        }
        files = SimpleNamespace(root=root, read=lambda args: records[(args["id"], args["cursor"])])
        rows = self.navigation.drive_tree(files, {"index-1"})
        self.assertEqual([r[0] for r in rows], ["Project", "Project / Draft.docx"])
        records[(folder, None)] = {"files": []}
        records[(root, "next")] = {
            "files": [{"id": leaf, "name": "Final.docx", "mimeType": "document"}]
        }
        rows = self.navigation.drive_tree(files, {"index-1"})
        self.assertEqual([r[0] for r in rows], ["Final.docx", "Project"])

    def test_notion_tree_checks_real_parent_and_does_not_copy_database_rows(self):
        root, child, outside, database = "1" * 32, "2" * 32, "3" * 32, "4" * 32
        records = {
            root: {
                "id": root,
                "parent": None,
                "content": f'<page url="https://notion.so/{child}">Project</page><page url="https://notion.so/{outside}">Forged reference</page><database url="https://notion.so/{database}">Requests</database>',
            },
            child: {
                "id": child,
                "parent": root,
                "content": "Private document content should not enter the index",
            },
            outside: {"id": outside, "parent": "5" * 32, "content": "Other section"},
        }
        pages = SimpleNamespace(root=root, fetch=lambda identity: records[identity])
        rows = self.navigation.notion_tree(pages, set())
        body = self.navigation.content(rows, [], "fixture")
        self.assertIn(MARKER, body)
        self.assertIn("Project", body)
        self.assertNotIn("Forged reference", body)
        self.assertNotIn("Private document content", body)
        self.assertIn("База данных", body)

    def test_incomplete_scan_retains_last_success_and_visible_failure(self):
        self.navigation.state.update(last_success=123, notion_url="https://notion.so/fixture")
        self.navigation.refresh = Mock(side_effect=OSError("private provider detail"))
        self.navigation.running = True
        self.navigation.run()
        status = self.navigation.status()
        self.assertEqual(status["last_success"], 123)
        self.assertFalse(status["running"])
        self.assertTrue(status["error"])
        self.assertNotIn("private provider detail", json.dumps(status))

    def test_incomplete_provider_listing_never_becomes_a_complete_index(self):
        files = SimpleNamespace(
            root="root-folder-1", read=lambda args: {"files": [], "incompleteSearch": True}
        )
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.navigation.drive_tree(files, set())

    def test_titles_are_plain_labels_and_incomplete_pagination_is_rejected(self):
        body = self.navigation.content(
            [("Name](https://evil.test) | thing", "page", "https://notion.so/" + "1" * 32)],
            [],
            "fixture",
        )
        self.assertIn(r"Name\]\(https://evil.test\)", body)
        files = SimpleNamespace(
            root="root-folder-1", read=lambda args: {"files": [], "nextPageToken": "same"}
        )
        with self.assertRaisesRegex(ValueError, "pagination"):
            self.navigation.drive_tree(files, set())
