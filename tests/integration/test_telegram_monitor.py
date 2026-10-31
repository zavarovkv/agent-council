"""Exercise the real pinned reader SDK offline."""

import subprocess
import unittest


class TelegramMonitorRuntimeTests(unittest.TestCase):
    def test_reader_dependencies_are_available_only_in_the_dedicated_interpreter(self):
        result = subprocess.run(
            [
                "/opt/telegram-reader/bin/python",
                "-c",
                "import telethon; from telethon.sessions import StringSession; assert telethon.__version__=='1.45.0'; assert StringSession().save()==''; print('sdk_ready')",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        self.assertEqual(result.stdout.strip(), "sdk_ready")
        import importlib.util

        self.assertIsNone(importlib.util.find_spec("telethon"))
