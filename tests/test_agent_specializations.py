"""Specialist discovery, additive setup and bounded skill loading."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_linear_agent import NOW, FakeHermes, FakeLinear, configuration, uid

from council_ops.config import OperationError
from council_ops.linear import configure, prepare_files
from linear_agent.service import Service
from linear_agent.store import Store
from linear_tools.skills import view


class SpecialistTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.value = configuration(self.home)

    def test_catalog_uses_real_registration_grants_and_authorized_task_context(self):
        store = Store(self.home, clock=lambda: NOW)
        linear = FakeLinear()
        for index, slug in enumerate(("qui-gon", "dexter")):
            store.install(slug, uid(1000 + index), uid(1), "fixture", "fixture", NOW + 5000)
        service = Service(self.home, store=store, linear=linear, hermes=FakeHermes())
        session, _ = service.authorize(
            service.registry(), service.registry().agents["qui-gon"], uid(10)
        )
        # Context binding is normally created when the native run is admitted.
        with patch.object(store, "context", return_value=session):
            catalog = service.agent_catalog(
                "qui-gon", {"run": "fixture", "session": session["hermes"]}
            )
            by_slug = {item["slug"]: item for item in catalog["agents"]}
            self.assertTrue(by_slug["dexter"]["delegation_allowed"])
            self.assertFalse(by_slug["marketing"]["registered"])
            self.assertFalse(by_slug["marketing"]["delegation_allowed"])
            self.assertFalse(by_slug["padme"]["registered"])
            self.assertFalse(by_slug["padme"]["delegation_allowed"])
            with store.database() as db:
                db.execute("DELETE FROM installations WHERE agent=?", ("dexter",))
            by_slug = {
                item["slug"]: item
                for item in service.agent_catalog(
                    "qui-gon", {"run": "fixture", "session": session["hermes"]}
                )["agents"]
            }
            self.assertFalse(by_slug["dexter"]["installed"])
            linear.sessions[uid(10)]["creator"]["id"] = uid(999)
            with self.assertRaises(PermissionError):
                service.agent_catalog("qui-gon", {"run": "fixture", "session": session["hermes"]})
        with self.assertRaises(PermissionError):
            service.agent_catalog("qui-gon", {"run": "forged", "session": "forged"})

    def test_additive_profile_preparation_preserves_existing_grants_and_rules(self):
        home = self.home / "profile"
        home.mkdir()
        plan = {k: self.value[k] for k in ("owner", "organization", "teams", "origin")}
        plan.update(schema=1, agents=["qui-gon", "dexter"])
        repository = Path(__file__).resolve().parents[1]
        args = ({"provider": "fixture", "default": "fixture"}, repository, os.getuid(), os.getgid())
        prepare_files(home, plan, *args)
        base = home / ".council/linear"
        grant = base / "dexter/auth.json"
        grant.write_text("own credential fixture")
        soul = base / "qui-gon/SOUL.md"
        soul.write_text("Reviewed private coordinator")
        key = (base / "bridge/qui-gon.env").read_bytes()
        expanded = {**plan, "agents": [*plan["agents"], "marketing"]}
        prepare_files(home, expanded, *args)
        self.assertEqual(grant.read_text(), "own credential fixture")
        self.assertEqual(soul.read_text(), "Reviewed private coordinator")
        self.assertEqual((base / "bridge/qui-gon.env").read_bytes(), key)
        self.assertFalse((base / "marketing/auth.json").exists())
        config = json.loads((base / "marketing/config.yaml").read_text())
        self.assertEqual(config["mcp_servers"], {})
        self.assertEqual(
            config["platform_toolsets"]["api_server"], ["web", "clarify", "council-linear"]
        )
        with self.assertRaises(OperationError):
            prepare_files(home, plan, *args)
        self.assertEqual(json.loads((base / "bridge/plan.json").read_text()), expanded)
        with_editor = {**expanded, "agents": [*expanded["agents"], "padme"]}
        prepare_files(home, with_editor, *args)
        self.assertEqual(grant.read_text(), "own credential fixture")
        self.assertEqual(soul.read_text(), "Reviewed private coordinator")
        self.assertEqual((base / "bridge/qui-gon.env").read_bytes(), key)
        self.assertFalse((base / "padme/auth.json").exists())
        editor = json.loads((base / "padme/config.yaml").read_text())
        self.assertEqual(editor["mcp_servers"], {})
        self.assertEqual(
            editor["platform_toolsets"]["api_server"], ["web", "clarify", "council-linear"]
        )

    def test_additive_credentials_preserve_old_policy_epoch_and_grants(self):
        home = self.home / "runtime"
        bridge = home / ".council/linear/bridge"
        bridge.mkdir(parents=True)
        old = copy.deepcopy(self.value)
        (bridge / "agents.json").write_text(json.dumps(old))
        (bridge / "agents.json").chmod(0o600)
        plan = {k: old[k] for k in ("owner", "organization", "teams", "origin")}
        plan.update(schema=1, agents=["qui-gon", "dexter", "marketing"])
        (bridge / "plan.json").write_text(json.dumps(plan))
        (bridge / "plan.json").chmod(0o600)
        (bridge / "marketing.env").write_text("API_SERVER_KEY=" + "3" * 64 + "\n")
        runtime = SimpleNamespace(settings=SimpleNamespace(data_dir=home))
        with (
            patch("council_ops.linear.sys.stdin.isatty", return_value=True),
            patch(
                "council_ops.linear.getpass.getpass",
                side_effect=["fixture-marketing", "m" * 32, "w" * 32],
            ),
            patch("council_ops.linear.snapshot", return_value=Path("/fixture/backup")),
            patch(
                "council_ops.linear.smoke",
                side_effect=lambda _r, _b, prepare_copy: prepare_copy(home),
            ),
            patch("council_ops.linear.identity", return_value=(os.getuid(), os.getgid())),
        ):
            receipt = configure(runtime, selected="marketing")
            with self.assertRaises(OperationError):
                configure(runtime, selected="marketing")
        current = json.loads((bridge / "agents.json").read_text())
        for field in ("enabled", "revision", "admission_since", "owner", "organization", "teams"):
            self.assertEqual(current[field], old[field])
        for slug in ("qui-gon", "dexter"):
            for field in old["agents"][slug]:
                if field != "delegates":
                    self.assertEqual(current["agents"][slug][field], old["agents"][slug][field])
        self.assertEqual(current["agents"]["qui-gon"]["delegates"], ["dexter", "marketing"])
        self.assertTrue(receipt["execution_enabled"])

    def test_skill_reader_lists_and_reads_only_own_reviewed_text(self):
        folder = self.home / "skills/research"
        (folder / "references").mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            "---\nname: research\ndescription: Check source evidence.\n---\nUse direct sources.\n"
        )
        (folder / "references/method.md").write_text("Read the original source.")
        (self.home / "auth.json").write_text("PRIVATE fixture credential")
        with patch.dict(os.environ, {"HERMES_HOME": str(self.home)}):
            self.assertEqual(json.loads(view({}))["skills"][0]["name"], "research")
            self.assertIn("direct sources", json.loads(view({"name": "research"}))["content"])
            self.assertIn(
                "original source",
                json.loads(view({"name": "research", "path": "references/method.md"}))["content"],
            )
            for args in (
                {"name": "../auth.json"},
                {"name": "research", "path": "../../auth.json"},
                {"name": "research", "path": "/auth.json"},
                {"name": "research", "path": "references/../SKILL.md"},
                {"name": "research", "path": "scripts/run.py"},
                {"name": "research", "path": "references/key.env"},
            ):
                self.assertIn("error", json.loads(view(args)))
            (folder / "references/leak.md").symlink_to(self.home / "auth.json")
            self.assertIn(
                "error", json.loads(view({"name": "research", "path": "references/leak.md"}))
            )
            (folder / "SKILL.md").write_text("x" * 65537)
            self.assertIn("error", json.loads(view({"name": "research"})))

    def test_skill_catalog_survives_upstream_categories_and_empty_directories(self):
        root = self.home / "skills"
        reviewed = root / "task-management"
        reviewed.mkdir(parents=True)
        metadata = "---\nname: task-management\ndescription: Manage tasks.\n---\nReviewed.\n"
        (reviewed / "SKILL.md").write_text(metadata)
        nested = root / "research/upstream-example"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text("Upstream text, not part of the reviewed catalog.")
        (root / "obsolete-skill").mkdir()
        with patch.dict(os.environ, {"HERMES_HOME": str(self.home)}):
            self.assertEqual(
                json.loads(view({})),
                {"skills": [{"name": "task-management", "description": "Manage tasks."}]},
            )
            self.assertEqual(json.loads(view({"name": "task-management"}))["content"], metadata)
            for name in ("research", "research/upstream-example", "obsolete-skill"):
                self.assertIn("error", json.loads(view({"name": name})))
            # Missing category metadata is ignored; invalid or linked skill
            # metadata still fails closed instead of silently hiding a skill.
            (root / "obsolete-skill/SKILL.md").write_text("Invalid metadata")
            self.assertIn("error", json.loads(view({})))
            (root / "obsolete-skill/SKILL.md").unlink()
            (root / "obsolete-skill/SKILL.md").symlink_to(self.home / "missing.md")
            self.assertIn("error", json.loads(view({})))
