"""Exercise provisioning and the official routing plugin against pinned Hermes."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ResearcherIntegrationTests(unittest.TestCase):
    def test_native_profile_plugin_and_route_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".hermes"
            home.mkdir()
            config = {
                "model": {"provider": "openai-codex", "default": "test-model"},
                "agent": {"reasoning_effort": "medium"},
                "dashboard": {"auth": "telegram-owner"},
            }
            # JSON is YAML; these fixtures are public fake values, never live credentials.
            (home / "config.yaml").write_text(json.dumps(config))
            (home / ".env").write_text("TELEGRAM_BOT_TOKEN=not-a-real-token\n")
            (home / "SOUL.md").write_text("private coordinator identity")
            (home / "memories").mkdir()
            (home / "memories" / "MEMORY.md").write_text("private coordinator memory")
            env = {**os.environ, "HOME": directory, "HERMES_HOME": str(home)}
            for _ in range(2):
                result = subprocess.run(
                    [sys.executable, "/opt/agent-council/provision-researcher.py"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-3000:])
            profile = home / "profiles" / "dexter"
            self.assertTrue((profile / "research").is_dir())
            # Native profile initialization may create a blank .env template.
            if (profile / ".env").exists():
                self.assertNotIn("not-a-real-token", (profile / ".env").read_text())
            self.assertIn("Dexter Jettster", (profile / "SOUL.md").read_text())
            self.assertNotIn("private coordinator", (profile / "SOUL.md").read_text())
            memory = profile / "memories" / "MEMORY.md"
            if memory.exists():
                self.assertNotIn("private coordinator memory", memory.read_text())
            # Real core discovery, schema extension and early rejection. No LLM
            # request, fake child, network connection or production state is used.
            probe = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    """
import json
from types import SimpleNamespace
from hermes_cli.plugins import PluginManager
from hermes_cli.config import read_user_config_raw
from tools.registry import registry
manager = PluginManager()
manager.discover_and_load()
from tools.delegate_tool import delegate_task
schema = registry.get_definitions({'delegate_task'})[0]['function']
profiles = schema['parameters']['properties']['tasks']['items']['properties']['profile']['enum']
assert profiles == ['dexter'], profiles
parent = SimpleNamespace(session_id='test', model='test-model', provider='openai-codex',
    tool_progress_callback=None, _delegate_spinner=None, request_overrides={}, _safe_print=lambda line: None)
rejected = json.loads(delegate_task(tasks=[{'goal': 'invalid route', 'profile': 'admin'}], parent_agent=parent))
assert 'must be one of dexter' in rejected['error'], rejected
config = read_user_config_raw()
assert config['dashboard']['auth'] == 'telegram-owner'
assert 'default' not in config['delegation']['backends']['profile_routes']
manager.unload()
print('Native researcher discovery and route policy: OK')
""",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr[-3000:])
