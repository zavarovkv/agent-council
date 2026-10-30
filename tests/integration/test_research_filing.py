"""Actual native board/run/attachment contracts; all cloud writes are fixtures."""

import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect_closing
from hermes_cli.plugins import PluginContext


class ResearchFilingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        environment = patch.dict(
            os.environ, {"HERMES_HOME": str(self.home), "HERMES_KANBAN_HOME": str(self.home)}
        )
        environment.start()
        self.addCleanup(environment.stop)
        root = Path("/opt/hermes/plugins/council-research")
        name = "council_research_fixture"
        spec = importlib.util.spec_from_file_location(
            name, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.module
        spec.loader.exec_module(self.module)
        self.native = __import__(name + ".native", fromlist=["execute"])
        self.tools = []
        self.commands = []

        class Context:
            def get_config(_self, key, default=None):
                return {"enabled": True, "board": "research"}.get(key, default)

            def register_tool(_self, **values):
                inspect.signature(PluginContext.register_tool).bind(None, **values)
                self.tools.append(values)

            def register_cli_command(_self, **values):
                inspect.signature(PluginContext.register_cli_command).bind(None, **values)
                self.commands.append(values)

        self.ctx = Context()
        self.module.register(self.ctx)
        (self.home / "profiles/dexter/research").mkdir(parents=True)
        (self.home / "profiles/dexter/config.yaml").write_text("{}")
        (self.home / "profiles/dexter/research/report.md").write_text(
            "# Evidence\n\nVerified fixture finding.\n"
        )
        (self.home / "council-workspace.json").write_text(
            json.dumps(
                {"knowledge_base": {"provider": "notion", "research": {"page_id": "1" * 32}}}
            )
        )
        kb.create_board("research", name="Fixture research")

    def task(self, complete=True):
        with connect_closing(board="research") as db:
            task = kb.create_task(
                db, title="Fixture research", assignee="dexter", initial_status="running"
            )
            if complete:
                self.assertTrue(kb.complete_task(db, task, result="Verified fixture summary"))
            return task

    def test_completed_native_run_keeps_one_attachment_and_verified_result_comment(self):
        task = self.task()

        def filed(store, record):
            store.save({**record, "state": "done", "page_id": "2" * 32})
            return {
                "filed": True,
                "url": "https://www.notion.so/" + "2" * 32,
                "task_id": task,
                "run_id": record["run_id"],
            }

        with patch.object(self.native.Notion, "file", side_effect=filed):
            for _ in range(2):
                result = self.native.execute(
                    self.ctx, {"action": "file", "task_id": task, "report_path": "report.md"}
                )
                self.assertTrue(result.get("filed"), result)
        with connect_closing(board="research") as db:
            self.assertEqual(kb.get_task(db, task).status, "done")
            self.assertEqual(len(kb.list_attachments(db, task)), 1)
            self.assertEqual(len(kb.list_comments(db, task)), 1)
            self.assertTrue(any(run.id == result["run_id"] for run in kb.list_runs(db, task)))

    def test_unfinished_task_and_escaping_or_symlinked_report_do_not_reach_notion(self):
        task = self.task(complete=False)
        with patch.object(self.native.Notion, "file") as filed:
            result = self.native.execute(
                self.ctx, {"action": "file", "task_id": task, "report_path": "report.md"}
            )
            self.assertFalse(result["filed"])
            completed = self.task()
            for path in ("../../config.yaml", "/etc/passwd"):
                result = self.native.execute(
                    self.ctx, {"action": "file", "task_id": completed, "report_path": path}
                )
                self.assertFalse(result["filed"])
            link = self.home / "profiles/dexter/research/link.md"
            link.symlink_to(self.home / "profiles/dexter/config.yaml")
            self.assertFalse(
                self.native.execute(
                    self.ctx, {"action": "file", "task_id": completed, "report_path": "link.md"}
                )["filed"]
            )
        filed.assert_not_called()

    def test_named_profile_cannot_reuse_coordinator_tool_or_mcp_grant(self):
        task = self.task()
        with patch("hermes_constants.get_hermes_home", return_value=self.home / "profiles/dexter"):
            result = self.native.execute(
                self.ctx, {"action": "file", "task_id": task, "report_path": "report.md"}
            )
        self.assertEqual(result["error"], "coordinator_only")

    def test_real_provisioner_and_native_cli_resume_an_empty_outbox_without_model_calls(self):
        (self.home / "config.yaml").write_text("{}")
        subprocess.run(
            [sys.executable, "/opt/agent-council/provision-research-workflow.py"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = subprocess.run(
            ["hermes", "research-filing", "flush"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        value = json.loads(result.stdout)
        self.assertEqual(value["completed"], 0)
        self.assertEqual(value["pending_or_conflicted"], 0)
