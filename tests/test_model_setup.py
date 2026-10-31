import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class SetupModelTests(unittest.TestCase):
    def test_both_model_logins_use_the_same_maintenance_lock_and_the_correct_container_identity(
        self,
    ):
        script = Path(__file__).resolve().parents[1] / "scripts/setup-model.sh"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ssh = root / "ssh"
            ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$COUNCIL_TEST_SSH_COMMAND"\n')
            ssh.chmod(0o755)
            output = root / "arguments"
            environment = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "COUNCIL_TEST_SSH_COMMAND": str(output),
            }
            for arguments in ([], ["secretary"], ["linear-executor"], ["dexter-executor"]):
                subprocess.run(["bash", str(script), *arguments], env=environment, check=True)
                command = output.read_text().splitlines()[-1]
                self.assertIn("flock -n /etc/agent-council/operations.lock", command)
                if arguments:
                    self.assertNotIn("--user hermes", command)
                    self.assertIn("exec " + arguments[0], command)
                else:
                    self.assertIn("exec --user hermes hermes", command)
