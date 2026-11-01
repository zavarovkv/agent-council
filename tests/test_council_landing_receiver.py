"""Validate forced-command parsing and archive limits without privileged calls."""

import hashlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/council-landing-deploy.py"
spec = importlib.util.spec_from_file_location("council_landing_receiver", SCRIPT)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


class ReceiverTests(unittest.TestCase):
    def test_rejects_any_other_command_before_reading_input(self):
        for command in (\n            "",\n            "ls",\n            "council-landing-deploy bad",\n            "council-landing-deploy " + "f" * 64 + ";id",\n        ):
            with self.subTest(command=command):
                with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": command}):
                    with self.assertRaises(SystemExit):
                        receiver.main()

    def test_rejects_bad_digest_and_oversize(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = b"example artifact"
            with (
                patch.object(receiver, "LOCK", str(Path(tmp) / "lock")),
                patch.dict(\n                    os.environ, {"SSH_ORIGINAL_COMMAND": "council-landing-deploy " + "0" * 64}\n                ),
                patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload))),
                patch.object(receiver.subprocess, "run") as run,
            ):
                with self.assertRaises(SystemExit):
                    receiver.main()
                run.assert_not_called()
            with (
                patch.object(receiver, "LOCK", str(Path(tmp) / "lock")),
                patch.object(receiver, "MAX_ARCHIVE", 2),
                patch.dict(\n                    os.environ,\n                    {\n                        "SSH_ORIGINAL_COMMAND": "council-landing-deploy "\n                        + hashlib.sha256(payload).hexdigest()\n                    },\n                ),
                patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload))),
                patch.object(receiver.subprocess, "run") as run,
            ):
                with self.assertRaises(SystemExit):
                    receiver.main()
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
