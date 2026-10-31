"""Exercise the real pinned SDK and native cron offline with disposable data."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


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

    def test_real_native_cron_prepare_activate_and_idempotent_reconfiguration(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "config.yaml").write_text(json.dumps({"timezone": "UTC"}))
            skill = home / "skills/travel-digest/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: travel-digest\ndescription: Offline fixture.\n---\nOffline fixture only.\n"
            )
            source = """
import os,sys,json
from pathlib import Path
sys.path.insert(0,'/opt/agent-council')
import cron.jobs as jobs
from telegram_monitor.schedule import provision
home=Path(os.environ['HERMES_HOME'])
first=provision(home,'fixture_source',20,10,jobs)
assert not first['enabled']
assert len(jobs.list_jobs(include_disabled=True))==2
second=provision(home,'fixture_source',20,10,jobs,activate=True)
third=provision(home,'fixture_source',20,10,jobs,activate=True)
assert second['jobs']==third['jobs']==first['jobs']
assert all(j['enabled'] for j in jobs.list_jobs(include_disabled=True))
print(json.dumps({'native_cron_verified':True}))
"""
            environment = {**os.environ, "HERMES_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
            result = subprocess.run(
                ["/opt/hermes/.venv/bin/python", "-c", source],
                env=environment,
                text=True,
                capture_output=True,
                check=True,
                timeout=30,
            )
            self.assertTrue(json.loads(result.stdout)["native_cron_verified"])
