"""A fresh Docker profile must have a browsable store without running a bot."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hermes_cli.web_server_sessions import _open_session_db_at_path


def initialize(home):
    subprocess.run(
        [sys.executable, "/opt/agent-council/initialize-profile.py"],
        env={**os.environ, "HERMES_HOME": str(home)},
        check=True,
        capture_output=True,
    )


class ProfileBootstrapTests(unittest.TestCase):
    def test_initialization_is_private_browsable_and_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            initialize(home)
            path = home / "state.db"
            self.assertGreater(path.stat().st_size, 0)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            database = _open_session_db_at_path(path, read_only=True)
            database.close()
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE council_test_marker(value TEXT)")
                connection.execute("INSERT INTO council_test_marker VALUES ('retained')")
            initialize(home)
            with sqlite3.connect(path) as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM council_test_marker").fetchone()[0],
                    "retained",
                )
