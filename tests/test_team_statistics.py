"""Public counters preserve attribution, privacy and failure semantics."""

import json
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from council_team.catalog import ROLES
from council_team.view import AVATARS, PROFILES, issue_counts, snapshot, token_counts
from linear_agent.statistics import collect, publish


class TeamStatisticsTests(unittest.TestCase):
    def test_published_portraits_share_png_format_and_square_dimensions(self):
        assets = Path(__file__).resolve().parents[1] / "assets/agents"
        self.assertEqual(set(AVATARS), {"qui-gon", "dexter", "lando", "padme"})
        for name in AVATARS.values():
            with self.subTest(name=name):
                self.assertEqual(Path(name).suffix, ".png")
                header = (assets / name).read_bytes()[:24]
                self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(header[12:16], b"IHDR")
                self.assertEqual(struct.unpack(">II", header[16:24]), (256, 256))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()

    def database(self, path, rows=()):
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE sessions(id TEXT,profile_name TEXT,input_tokens INTEGER,output_tokens INTEGER,system_prompt TEXT)"
            )
            db.executemany(
                "INSERT INTO sessions VALUES(?,?,?,?,?)", [(*row, "PRIVATE prompt") for row in rows]
            )

    def fixture(self):
        self.database(
            self.home / "state.db", [("main", "default", 100, 20), ("research", "dexter", 30, 4)]
        )
        for name in PROFILES:
            if name != "default":
                self.database(self.home / "profiles" / name / "state.db")
        for slug in ROLES:
            self.database(self.home / ".council/linear" / slug / "state.db")

    def test_tokens_follow_recorded_profile_and_do_not_double_count_copied_sessions(self):
        self.fixture()
        with sqlite3.connect(self.home / "profiles/dexter/state.db") as db:
            db.execute(
                "INSERT INTO sessions VALUES('research','dexter',30,4,'PRIVATE duplicated prompt')"
            )
        result = token_counts(self.home)
        self.assertEqual(result, {"qui-gon": 120, "dexter": 34, "marketing": 0, "padme": 0})
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_missing_or_symlinked_database_is_unknown_not_zero(self):
        self.fixture()
        path = self.home / "profiles/padme/state.db"
        path.unlink()
        path.symlink_to(self.home / "state.db")
        self.assertIsNone(token_counts(self.home)["padme"])
        (self.home / "state.db").write_text("not SQLite")
        self.assertTrue(all(value is None for value in token_counts(self.home).values()))

    def test_public_snapshot_never_exposes_private_config_or_session_content(self):
        self.fixture()
        paths = [self.home / "config.yaml"]
        paths += [self.home / "profiles" / p / "config.yaml" for p in PROFILES if p != "default"]
        paths += [self.home / ".council/linear" / s / "config.yaml" for s in ROLES]
        config = {
            "model": {"default": "gpt-6-astra", "api_key": "PRIVATE secret"},
            "agent": {"reasoning_effort": "high"},
            "system_prompt": "PRIVATE policy",
        }
        for path in paths:
            path.write_text(json.dumps(config))
        value = snapshot(self.home, lambda path: json.loads(path.read_text()), now=1000)
        self.assertEqual(value["agents"][0]["model"], {"name": "GPT-6 Astra", "effort": "high"})
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertNotIn("updated_at", value)
        self.assertIsNone(value["agents"][0]["closed_tasks"])

    def test_stale_or_invalid_linear_snapshot_does_not_publish_plausible_zero(self):
        path = self.home / ".council/linear/bridge/public-team.json"
        path.parent.mkdir(parents=True)
        value = {
            "schema": 1,
            "updated_at": 100,
            "counts": dict.fromkeys(ROLES, 5),
            "connected": dict.fromkeys(ROLES, True),
            "private": "PRIVATE text",
        }
        path.write_text(json.dumps(value))
        counts, connected = issue_counts(self.home, 200)
        self.assertEqual(counts["dexter"], 5)
        self.assertTrue(connected["dexter"])
        self.assertTrue(all(x is None for x in issue_counts(self.home, 1200)[0].values()))
        value["counts"]["dexter"] = True
        path.write_text(json.dumps(value))
        self.assertTrue(all(x is None for x in issue_counts(self.home, 200)[0].values()))

    def service(self, pages):
        registry = SimpleNamespace(
            organization="org",
            teams={"team"},
            enabled=True,
            agents={s: SimpleNamespace(slug=s) for s in ("qui-gon", "dexter")},
        )
        installed = {s: {"app_user": "user-" + s, "organization": "org"} for s in registry.agents}
        return SimpleNamespace(
            home=self.home,
            registry=lambda: registry,
            clock=lambda: 100,
            store=SimpleNamespace(installation=installed.get),
            linear=SimpleNamespace(query=Mock(side_effect=pages)),
        )

    def page(self, rows, more=False, cursor=None):
        return {"issues": {"nodes": rows, "pageInfo": {"hasNextPage": more, "endCursor": cursor}}}

    def test_closed_issue_counts_paginate_and_credit_only_one_executor(self):
        row = {"id": "a", "delegate": {"id": "user-dexter"}, "assignee": {"id": "user-qui-gon"}}
        service = self.service(
            [
                self.page([row], True, "next"),
                self.page([row, {"id": "b", "delegate": None, "assignee": {"id": "user-qui-gon"}}]),
            ]
        )
        value = collect(service)
        self.assertEqual(
            value["counts"], {"qui-gon": 1, "dexter": 1, "marketing": None, "padme": None}
        )
        self.assertIn("includeArchived:true", service.linear.query.call_args.args[1])
        self.assertEqual(service.linear.query.call_args.args[2]["after"], "next")
        self.assertNotIn("user-dexter", json.dumps(value))

    def test_failed_refresh_keeps_last_successful_aggregate(self):
        path = self.home / "public-team.json"
        path.write_text("previous verified aggregate")
        service = self.service([RuntimeError("PRIVATE upstream error")])
        with self.assertRaises(RuntimeError):
            publish(service)
        self.assertEqual(path.read_text(), "previous verified aggregate")
