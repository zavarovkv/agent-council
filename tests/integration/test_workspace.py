"""Real provisioner and pinned Hermes plugin discovery, without cloud calls."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class WorkspaceIntegrationTests(unittest.TestCase):
    def test_all_profiles_receive_tools_without_copied_cloud_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "profile"
            profiles = [home] + [home / "profiles" / role for role in ("dexter", "lando", "padme")]
            executors = [
                home / ".council/linear" / role
                for role in ("qui-gon", "dexter", "marketing", "padme")
            ]
            for target in profiles + executors:
                target.mkdir(parents=True, exist_ok=True)
                config = {
                    "model": {"provider": "openai-codex", "default": "test-model"},
                    "platform_toolsets": {"api_server": ["web", "clarify", "council-linear"]},
                    "plugins": {"enabled": ["council-linear"] if target in executors else []},
                }
                (target / "config.yaml").write_text(json.dumps(config))
            (home / "council-workspace.json").write_text(
                json.dumps(
                    {
                        "knowledge_base": {"provider": "notion", "page_id": "1" * 32},
                        "files": {"provider": "google_drive", "folder_id": "folder-fixture"},
                    }
                )
            )
            (home / "google_token.json").write_text("private fake grant")
            env = {**os.environ, "HOME": directory, "HERMES_HOME": str(home)}
            script = "/opt/agent-council/provision-workspace.py"
            first = subprocess.run(
                [sys.executable, script], env=env, capture_output=True, text=True, timeout=30
            )
            self.assertEqual(first.returncode, 0, first.stderr[-2000:])
            policy = (home / ".council/workspace/access.json").read_bytes()
            second = subprocess.run(
                [sys.executable, script], env=env, capture_output=True, text=True, timeout=30
            )
            self.assertEqual(second.returncode, 0, second.stderr[-2000:])
            self.assertEqual(policy, (home / ".council/workspace/access.json").read_bytes())
            for target in profiles + executors:
                self.assertEqual((target / "workspace-client.json").stat().st_mode & 0o777, 0o600)
                if target != home:
                    self.assertFalse((target / "google_token.json").exists())
                    self.assertFalse((target / "mcp-tokens").exists())
                probe = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        """
import json
from hermes_cli.plugins import PluginManager
from tools.registry import registry
from council_workspace.client import settings
settings()
manager = PluginManager()
manager.discover_and_load()
definitions = registry.get_definitions({'council_workspace'})
assert len(definitions) == 1, definitions
assert definitions[0]['function']['name'] == 'council_workspace'
manager.unload()
print('workspace tool available')
""",
                    ],
                    env={**env, "HERMES_HOME": str(target)},
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                self.assertEqual(probe.returncode, 0, str(target) + probe.stderr[-3000:])
            self.assertIn(
                "gateway:startup", (home / "hooks/council-workspace/HOOK.yaml").read_text()
            )
            hook = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    """
import asyncio, json, urllib.request
from council_workspace.client import settings
from council_workspace import server
from gateway.hooks import HookRegistry
hooks = HookRegistry()
hooks.discover_and_load()
asyncio.run(hooks.emit('gateway:startup'))
assert server._server is not None
request = urllib.request.Request('http://127.0.0.1:9123/workspace', data=b'{"action":"context"}',
    headers={'Authorization': 'Bearer ' + settings()['key'], 'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=3) as response:
    assert json.load(response)['notion_root'] == '1' * 32
server._server.shutdown()
server._server.server_close()
print('Native hook and authenticated workspace listener ready')
""",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(hook.returncode, 0, hook.stderr[-3000:] + hook.stdout[-3000:])
