"""Real filesystem/SQLite regressions for canonical instruction installation."""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from council_ops.config import OperationError
from council_ops.instructions import collect, export, install_files

REVISION = "a" * 40


class CompositionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source, self.home = self.root / "source", self.root / "home"
        self.home.mkdir()
        for relative, text in {
            "assistant/SOUL.md": "Current role identity",
            "assistant/skills/method/SKILL.md": "Role method",
            "shared/skills/workflow/SKILL.md": "Canonical workflow",
            "shared/skills/workflow/references/example.md": "Canonical reference",
            "shared/linear-contract.md": "Channel contract",
            "adapters/linear/qui-gon.md": "Tool adapter",
            "linear/qui-gon/skills-source.json": '{"role":"assistant"}',
            "galen/SOUL.md": "Draft must not be installed",
        }.items():
            self.write(relative, text)
        self.write(
            "requires.json",
            json.dumps(
                {
                    "schema": 1,
                    "capabilities": [
                        "instruction-bundles-v2",
                        "instruction-composition-v1",
                        "agent-specialists-v1",
                    ],
                }
            ),
        )
        self.write(
            "bundle.json",
            json.dumps(
                {
                    "schema": 1,
                    "roles": ["assistant", "linear"],
                    "drafts": ["galen"],
                    "resources": ["shared", "adapters"],
                }
            ),
        )
        self.manifest = {
            "schema": 1,
            "shared": {"roles": ["assistant"], "skills": ["workflow"]},
            "linear": {"qui-gon": "qui-gon"},
        }
        self.save_manifest()
        profile = self.home / ".council/linear/qui-gon"
        profile.mkdir(parents=True)
        (profile / "config.yaml").write_text("model: unchanged")
        self.runtime = Mock(settings=SimpleNamespace(data_dir=self.home))

    def write(self, relative, text):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def save_manifest(self):
        self.write("composition.json", json.dumps(self.manifest))

    def install(self):
        files = collect(self.source, self.home)
        install_files(self.home, files, REVISION, os.getuid(), os.getgid())
        return files

    def test_canonical_edits_reach_both_profiles_without_generation(self):
        files = self.install()
        self.assertEqual(
            files["skills/workflow/SKILL.md"],
            files[".council/linear/qui-gon/skills/workflow/SKILL.md"],
        )
        self.assertEqual(
            files["skills/workflow/references/example.md"].data, b"Canonical reference"
        )
        self.assertFalse(any("galen" in name for name in files))
        self.write("assistant/SOUL.md", "Updated role")
        self.write("shared/skills/workflow/SKILL.md", "Updated shared method")
        files = self.install()
        self.assertEqual(
            files[".council/linear/qui-gon/SOUL.md"].data,
            b"Updated role\n\nChannel contract\n\nTool adapter\n",
        )
        self.assertEqual(files["skills/workflow/SKILL.md"].data, b"Updated shared method")
        self.assertFalse((self.source / "linear/qui-gon/SOUL.md").exists())

    def test_stale_generated_soul_and_role_skill_collisions_are_rejected(self):
        path = self.write("linear/qui-gon/SOUL.md", "Outdated generated rules")
        with self.assertRaisesRegex(OperationError, "generated Linear"):
            collect(self.source, self.home)
        path.unlink()
        self.write("assistant/skills/workflow/SKILL.md", "Old copy")
        with self.assertRaisesRegex(OperationError, "Duplicate"):
            collect(self.source, self.home)

    def test_draft_and_path_escape_are_not_composition_sources(self):
        self.manifest["shared"]["roles"].append("galen")
        self.save_manifest()
        with self.assertRaises(OperationError):
            collect(self.source, self.home)
        self.manifest["shared"]["roles"].remove("galen")
        self.manifest["linear"]["qui-gon"] = "../../private"
        self.save_manifest()
        with self.assertRaises(OperationError):
            collect(self.source, self.home)
        self.manifest["linear"]["qui-gon"] = "qui-gon"
        self.save_manifest()
        path = self.source / "shared/linear-contract.md"
        path.unlink()
        path.symlink_to(self.source / "assistant/SOUL.md")
        with self.assertRaises(OperationError):
            collect(self.source, self.home)

    def test_shared_export_maps_back_and_refuses_divergence_or_composed_soul_edits(self):
        self.install()
        for relative in (
            "skills/workflow/SKILL.md",
            ".council/linear/qui-gon/skills/workflow/SKILL.md",
        ):
            (self.home / relative).write_text("Reviewed local update")
        with patch("council_ops.instructions.checkout"):
            export(self.runtime, self.source, REVISION)
            self.assertEqual(
                (self.source / "shared/skills/workflow/SKILL.md").read_text(),
                "Reviewed local update",
            )
            self.assertFalse((self.source / "assistant/skills/workflow").exists())
            (self.home / "skills/workflow/SKILL.md").write_text("Divergent update")
            with self.assertRaisesRegex(OperationError, "diverged"):
                export(self.runtime, self.source, REVISION)
            (self.home / "skills/workflow/SKILL.md").write_text("Reviewed local update")
            (self.home / ".council/linear/qui-gon/SOUL.md").write_text("Manual composed edit")
            with self.assertRaisesRegex(OperationError, "Composed SOUL"):
                export(self.runtime, self.source, REVISION)

    def test_retired_managed_skill_is_removed_without_touching_untracked_resources(self):
        self.install()
        extra = self.home / "skills/method/local-note.md"
        extra.write_text("Preserve operator work")
        shutil.rmtree(self.source / "assistant/skills/method")
        self.install()
        self.assertFalse((self.home / "skills/method/SKILL.md").exists())
        self.assertEqual(extra.read_text(), "Preserve operator work")
        self.assertFalse((self.home / ".council/linear/qui-gon/skills/method").exists())

    def test_changed_rules_refresh_cache_but_preserve_conversation_memory_and_grants(self):
        self.install()
        database = self.home / "state.db"
        with sqlite3.connect(database) as db:
            db.executescript(
                "CREATE TABLE sessions(id TEXT, system_prompt TEXT, system_prompt_hash TEXT, tool_names TEXT); CREATE TABLE messages(text TEXT); INSERT INTO sessions VALUES('stable-id','Old rules','old-hash','original-tool-pin'); INSERT INTO messages VALUES('Keep conversation');"
            )
        snapshot = self.home / ".skills_prompt_snapshot.json"
        snapshot.write_text("Old skill index")
        memory = self.home / "MEMORY.md"
        memory.write_text("Keep memory")
        self.install()  # Identical install must not break the cache.
        self.assertTrue(snapshot.exists())
        self.write("assistant/SOUL.md", "Short answers, new rules")
        self.install()
        with sqlite3.connect(database) as db:
            self.assertEqual(
                db.execute("SELECT * FROM sessions").fetchone(),
                ("stable-id", None, None, "original-tool-pin"),
            )
            self.assertEqual(
                db.execute("SELECT * FROM messages").fetchone(), ("Keep conversation",)
            )
        self.assertFalse(snapshot.exists())
        self.assertEqual(memory.read_text(), "Keep memory")
        self.assertEqual(
            (self.home / ".council/linear/qui-gon/config.yaml").read_text(), "model: unchanged"
        )

    def test_unknown_cache_schema_and_linked_cache_fail_closed(self):
        self.install()
        database = self.home / "state.db"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE sessions(id TEXT)")
        self.write("assistant/SOUL.md", "Changed")
        with self.assertRaisesRegex(OperationError, "schema"):
            self.install()
        database.unlink()
        (self.home / ".skills_prompt_snapshot.json").symlink_to(self.source / "assistant/SOUL.md")
        with self.assertRaisesRegex(OperationError, "linked"):
            self.install()
