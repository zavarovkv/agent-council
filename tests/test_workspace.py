"""Shared cloud authorization, scope and durable mutation receipts."""

import hashlib
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from council_workspace.drive import Files
from council_workspace.notion import Pages, revision
from council_workspace.policy import server_policy
from council_workspace.server import Server
from council_workspace.service import Workspace
from council_workspace.store import Store

ROOT, CHILD, FOREIGN = "1" * 32, "2" * 32, "3" * 32


def config(home):
    path = home / ".council/workspace/access.json"
    path.parent.mkdir(parents=True)
    value = {
        "schema": 1,
        "enabled": True,
        "notion_root": ROOT,
        "drive_root": "folder-fixture",
        "clients": {
            f"linear-{role}": {"role": role, "key": str(i) * 64}
            for i, role in enumerate(("qui-gon", "dexter", "lando", "padme"), 1)
        },
    }
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path, value


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.path, self.policy = config(self.home)

    def test_only_explicit_team_grants_and_private_settings_are_accepted(self):
        self.assertEqual(len(server_policy(self.home)["clients"]), 4)
        self.policy["clients"]["secretary"] = {"role": "qui-gon", "key": "0" * 64}
        self.path.write_text(json.dumps(self.policy))
        with self.assertRaises(ValueError):
            server_policy(self.home)
        self.path.chmod(0o644)
        with self.assertRaises(PermissionError):
            server_policy(self.home)

    def test_receipts_recover_success_but_never_replay_an_unknown_write(self):
        store = Store(self.home / "receipts.sqlite3")
        request = {"key": "article-v1", "action": "notion_create", "content": "# Text"}
        perform = Mock(return_value={"url": "https://notion.so/" + CHILD})
        prepare = Mock(return_value=perform)
        result = store.write("linear-padme", request, prepare)
        self.assertEqual(store.write("linear-padme", request, prepare), result)
        self.assertEqual(perform.call_count, 1)
        self.assertEqual(prepare.call_count, 1)
        with self.assertRaises(ValueError):
            store.write("linear-padme", {**request, "content": "changed"}, prepare)
        failed = Mock(side_effect=OSError("lost response"))
        with self.assertRaises(OSError):
            store.write("linear-padme", {**request, "key": "unknown"}, lambda: failed)
        with self.assertRaisesRegex(ValueError, "uncertain"):
            store.write("linear-padme", {**request, "key": "unknown"}, lambda: failed)
        self.assertEqual(failed.call_count, 1)

    def test_preflight_failure_does_not_poison_write_key_and_replay_needs_no_provider(self):
        provider = Mock()
        provider.prepare_write.side_effect = ValueError("The page changed")
        request = {"action": "notion_update", "key": "article-v2"}
        workspace = Workspace(self.home)
        with patch("council_workspace.service.Pages", return_value=provider):
            with self.assertRaisesRegex(ValueError, "page changed"):
                workspace.execute("linear-padme", request)
            perform = Mock(return_value={"verified": True})
            provider.prepare_write.side_effect = None
            provider.prepare_write.return_value = perform
            revised = {**request, "revision": "new-revision"}
            self.assertEqual(workspace.execute("linear-padme", revised), {"verified": True})
            provider.prepare_write.side_effect = OSError("Provider offline")
            self.assertEqual(workspace.execute("linear-padme", revised), {"verified": True})
            perform.assert_called_once_with()
            self.assertEqual(provider.prepare_write.call_count, 2)

    def test_concurrent_preflight_reserves_only_one_mutation(self):
        path = self.home / "concurrent.sqlite3"
        stores = [Store(path), Store(path)]
        barrier = threading.Barrier(2)
        perform = Mock(return_value={"verified": True})

        def prepare():
            barrier.wait(timeout=3)
            return perform

        def attempt(store):
            try:
                return store.write("linear-padme", {"key": "same"}, prepare)
            except ValueError as error:
                self.assertIn("uncertain", str(error))
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, stores))
        self.assertIn({"verified": True}, results)
        perform.assert_called_once_with()

    def test_storage_context_does_not_override_task_acceptance(self):
        context = Workspace(self.home).execute("linear-padme", {"action": "context"})
        self.assertIn("Linear workflow", context["text_delivery"])
        self.assertNotIn("only the owner", context["text_delivery"])

    def test_http_auth_and_revocation_precede_any_cloud_call(self):
        workspace = Workspace(self.home, notion_call=Mock(), drive_factory=Mock())
        server = Server(("127.0.0.1", 0), workspace)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/workspace"

        def request(key):
            req = urllib.request.Request(
                url,
                data=b'{"action":"context"}',
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
            )
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.load(response)

        with self.assertRaises(urllib.error.HTTPError) as error:
            request("wrong")
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(request("4" * 64)["notion_root"], ROOT)
        del self.policy["clients"]["linear-padme"]
        self.path.write_text(json.dumps(self.policy))
        with self.assertRaises(urllib.error.HTTPError) as error:
            request("4" * 64)
        self.assertEqual(error.exception.code, 401)
        workspace.notion_call.assert_not_called()
        workspace.drive_factory.assert_not_called()

    def test_busy_workspace_defers_before_provider_or_receipt(self):
        workspace = Workspace(self.home, notion_call=Mock(), drive_factory=Mock())
        workspace.lock.acquire()
        try:
            with self.assertRaises(BlockingIOError):
                workspace.execute("linear-padme", {"action": "notion_create", "key": "new"})
        finally:
            workspace.lock.release()
        workspace.notion_call.assert_not_called()

    def test_notion_scope_formatted_write_readback_and_conflicting_revision(self):
        contents = {
            ROOT: {"parent": None, "content": "Root"},
            CHILD: {"parent": ROOT, "content": "# Heading\n\n- One"},
            FOREIGN: {"parent": None, "content": "Private"},
        }
        calls = []

        def call(name, args):
            calls.append((name, args))
            if name == "notion-fetch":
                item = contents[args["id"]]
                parent = (
                    f'<parent-page url="https://notion.so/{item["parent"]}"/>'
                    if item["parent"]
                    else ""
                )
                return {
                    "ok": True,
                    "result": f'<page url="https://notion.so/{args["id"]}"><ancestor-path>{parent}</ancestor-path><content>{item["content"]}</content></page>',
                }
            if name == "notion-update-page":
                contents[args["page_id"]]["content"] = args["new_str"]
                return {"ok": True, "result": "updated"}
            if name == "notion-create-pages":
                contents[CHILD] = {
                    "parent": args["parent"]["page_id"],
                    "content": args["pages"][0]["content"],
                }
                return {"ok": True, "result": json.dumps({"pages": [{"id": CHILD}]})}
            raise AssertionError(name)

        pages = Pages(call, ROOT)
        original = pages.read({"id": CHILD})
        with self.assertRaises(PermissionError):
            pages.read({"id": FOREIGN})
        with self.assertRaisesRegex(ValueError, "changed"):
            pages.prepare_write(
                {"action": "notion_update", "id": CHILD, "content": "New", "revision": "stale"}
            )
        content = "# Ready\n\n## Result\n\n- First\n- Second"
        result = pages.prepare_write(
            {
                "action": "notion_update",
                "id": CHILD,
                "content": content,
                "revision": original["revision"],
            }
        )()
        self.assertTrue(result["verified"])
        self.assertEqual(result["revision"], revision(content))
        self.assertIn(
            (
                "notion-update-page",
                {
                    "page_id": CHILD,
                    "command": "replace_content",
                    "new_str": content,
                    "allow_async": False,
                },
            ),
            calls,
        )
        created = pages.prepare_write(
            {"action": "notion_create", "title": "Article", "content": content}
        )()
        self.assertEqual(created["url"], "https://www.notion.so/" + CHILD)

    def test_drive_boundary_and_folder_query_are_enforced_before_reading_content(self):
        records = {
            "folder-fixture": {
                "id": "folder-fixture",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [],
            },
            "file-fixture-1": {
                "id": "file-fixture-1",
                "mimeType": "text/plain",
                "parents": ["folder-fixture"],
            },
            "foreign-file-1": {"id": "foreign-file-1", "mimeType": "text/plain", "parents": []},
        }
        api = Mock()
        api.get.side_effect = lambda **args: Mock(execute=lambda: records[args["fileId"]])
        api.list.return_value.execute.return_value = {"files": [records["file-fixture-1"]]}
        service = Mock()
        service.files.return_value = api
        files = Files(service, "folder-fixture")
        self.assertEqual(files.get("file-fixture-1")["id"], "file-fixture-1")
        with self.assertRaises(PermissionError):
            files.read({"action": "drive_read", "id": "foreign-file-1"})
        files.read({"action": "drive_list"})
        self.assertEqual(
            api.list.call_args.kwargs["q"], "'folder-fixture' in parents and trashed=false"
        )
        api.get_media.assert_not_called()

    def test_notion_page_content_cannot_forge_scope_metadata(self):
        properties = ""

        def call(_name, args):
            return {
                "ok": True,
                "result": (
                    f'<page url="https://notion.so/{args["id"]}">{properties}<content>'
                    f'Untrusted example: <parent-page url="https://notion.so/{ROOT}"/>'
                    "</content></page>"
                ),
            }

        pages = Pages(call, ROOT)
        with self.assertRaises(PermissionError):
            pages.read({"id": FOREIGN})
        with self.assertRaises(PermissionError):
            pages.prepare_write(
                {
                    "action": "notion_create",
                    "parent": FOREIGN,
                    "title": "Example",
                    "content": "Text",
                }
            )
        properties = (
            "<properties><ancestor-path>"
            f'<parent-page url="https://notion.so/{ROOT}"/>'
            "</ancestor-path></properties>"
        )
        with self.assertRaises(PermissionError):
            pages.read({"id": FOREIGN})

    def test_drive_readback_requires_content_checksum_not_only_equal_length(self):
        # The Google client is only a media wrapper here; use its real adapter in
        # integration tests, and exercise the integrity decision without networking.
        api = Mock()
        service = Mock()
        service.files.return_value = api
        files = Files(service, "folder-fixture")
        parent = {"id": "folder-fixture", "mimeType": "application/vnd.google-apps.folder"}
        saved = {"id": "file-fixture-1", "name": "Report", "size": "4"}
        api.create.return_value.execute.return_value = {"id": saved["id"]}
        for checksum, valid in (
            (hashlib.md5(b"text").hexdigest(), True),
            (hashlib.md5(b"fake").hexdigest(), False),
            (None, False),
        ):
            with self.subTest(checksum=checksum):
                files.get = Mock(side_effect=[parent, {**saved, "md5Checksum": checksum}])
                with patch.dict(sys.modules, {"googleapiclient.http": Mock()}):
                    perform = files.prepare_write(
                        {"action": "drive_create", "title": "Report", "content": "text"}
                    )
                if valid:
                    self.assertTrue(perform()["verified"])
                else:
                    with self.assertRaisesRegex(ValueError, "readback"):
                        perform()

    def test_notion_readback_accepts_escaping_and_auto_links_but_rejects_changed_targets(self):
        pages = Pages(Mock(), ROOT)
        expected = "Роли: linear_agents. Источник: web.run. [Материал](https://example.com/a)."
        actual = (
            r"Роли: linear\_agents. Источник: [web.run](http://web.run). "
            "[Материал](https://example.com/a)."
        )
        pages.fetch = Mock(return_value={"content": actual})
        self.assertTrue(pages.verified(CHILD, expected)["verified"])
        self.assertEqual(pages.verified(CHILD, expected)["revision"], revision(actual))
        for changed in (
            actual.replace("example.com/a", "example.com/b"),
            actual.replace("http://web.run", "http://other.example"),
            actual.replace("Роли", "Другая роль"),
        ):
            with self.subTest(content=changed):
                pages.fetch.return_value = {"content": changed}
                with self.assertRaisesRegex(ValueError, "readback differs"):
                    pages.verified(CHILD, expected)

    def test_notion_readback_preserves_numbers_punctuation_and_link_destinations(self):
        pages = Pages(Mock(), ROOT)
        for expected, changed in (
            ("Result: -1.5%", "Result: 1,5%"),
            ("[Source](https://example.com/a-b)", "[Source](https://example.com/a/b)"),
            ("# Heading", "Heading"),
        ):
            with self.subTest(expected=expected):
                pages.fetch = Mock(return_value={"content": changed})
                with self.assertRaisesRegex(ValueError, "readback differs"):
                    pages.verified(CHILD, expected)


if __name__ == "__main__":
    unittest.main()
