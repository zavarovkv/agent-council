"""A fresh Docker profile must have a browsable store without running a bot."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import yaml
from hermes_cli.web_server_sessions import _open_session_db_at_path


def initialize(home):
    subprocess.run(
        [sys.executable, "/opt/agent-council/initialize-profile.py"],
        env={**os.environ, "HERMES_HOME": str(home)},
        check=True,
        capture_output=True,
    )


class ProfileBootstrapTests(unittest.TestCase):
    def test_private_skill_metadata_is_verified_offline_before_activation(self):
        spec = importlib.util.spec_from_file_location(
            "council_profile_fixture", "/opt/agent-council/verify-profile.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / ".council").mkdir()
            skill = home / "skills/example/SKILL.md"
            skill.parent.mkdir(parents=True)
            (home / ".council/instructions.json").write_text(
                json.dumps({"files": {"skills/example/SKILL.md": "fixture"}})
            )
            for invalid in (
                "No metadata",
                "---\nname: example\n---\nBody",
                "---\nname: [invalid\n---\nBody",
            ):
                skill.write_text(invalid)
                with self.subTest(value=invalid), self.assertRaises((ValueError, yaml.YAMLError)):
                    module.verify_managed_skills(home)
            skill.write_text("---\nname: example\ndescription: Fixture skill\n---\nValid body")
            module.verify_managed_skills(home)

    def test_initialization_is_private_browsable_and_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            initialize(home)
            path = home / "state.db"
            self.assertGreater(path.stat().st_size, 0)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            database = _open_session_db_at_path(path, read_only=True)
            database.close()
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE council_test_marker(value TEXT)")
                connection.execute("INSERT INTO council_test_marker VALUES ('retained')")
            initialize(home)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM council_test_marker").fetchone()[0],
                    "retained",
                )
