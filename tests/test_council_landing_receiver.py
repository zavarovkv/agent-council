"""Validate forced-command parsing and archive limits without privileged calls."""

import hashlib
import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/council-landing-deploy.py"
spec = importlib.util.spec_from_file_location("council_landing_receiver", SCRIPT)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


class ReceiverTests(unittest.TestCase):
    def test_rejects_any_other_command_before_reading_input(self):
        for command in (
            "",
            "ls",
            "council-landing-deploy bad",
            "council-landing-deploy " + "f" * 64 + ";id",
        ):
            with self.subTest(command=command):
                with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": command}):
                    with self.assertRaises(SystemExit):
                        receiver.main()

    def test_rejects_bad_digest_and_oversize(self):
        payload = b"example artifact"
        with (
            patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "council-landing-deploy " + "0" * 64}),
            patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload))),
            patch.object(receiver.subprocess, "run") as run,
        ):
            with self.assertRaises(SystemExit):
                receiver.main()
            run.assert_not_called()
        with (
            patch.object(receiver, "MAX_ARCHIVE", 2),
            patch.dict(
                os.environ,
                {
                    "SSH_ORIGINAL_COMMAND": "council-landing-deploy "
                    + hashlib.sha256(payload).hexdigest()
                },
            ),
            patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload))),
            patch.object(receiver.subprocess, "run") as run,
        ):
            with self.assertRaises(SystemExit):
                receiver.main()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
