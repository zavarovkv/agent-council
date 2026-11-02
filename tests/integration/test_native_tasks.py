"""Exercise native task provisioning and plugin visibility in the pinned runtime."""

import os
import subprocess
import sys
import tempfile
import unittest


class NativeTaskRuntimeTests(unittest.TestCase):
    def test_profile_setup_preserves_oauth_and_limits_personal_linear_to_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            program = r"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from ruamel.yaml import YAML
from council_recurring.client import atomic_json
from council_tasks import register, settings
home = Path(__import__('os').environ['HERMES_HOME'])
def uid(n):
    return str(__import__('uuid').UUID(int=n))
atomic_json(home/'.council/linear/bridge/agents.json', {
    'schema':1,'enabled':True,'organization':uid(1),'owner':uid(2),'teams':[uid(3)],
    'origin':'https://fixture.example.test','revision':'a'*32,'admission_since':1,
    'agents':{'qui-gon':{'client_id':uid(100),'client_secret':'fixture-secret-12345',
        'webhook_secret':'fixture-webhook-12345','executor_key':'1'*64,
        'executor_url':'http://linear-executor:8642','delegates':[]}}})
original = {'model':{'default':'fixture-model'}, 'plugins':{'enabled':['another-plugin']},
    'mcp_servers':{'linear':{'url':'https://mcp.linear.app/mcp','auth':'oauth'},
                   'notion':{'url':'https://mcp.notion.com/mcp','auth':'oauth'}}}
with (home/'config.yaml').open('w') as stream:
    YAML().dump(original, stream)
spec=importlib.util.spec_from_file_location('native_setup','/opt/agent-council/provision-native-tasks.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
assert setup.configure(home)['personal_linear_read_only']
first = settings(home)
assert setup.configure(home)['native_tasks_configured'] and settings(home)==first
value=YAML().load((home/'config.yaml').read_text())
assert value['model']==original['model']
assert value['auxiliary']['background_review']['enabled'] is False
assert value['mcp_servers']['notion']==original['mcp_servers']['notion']
linear = dict(value['mcp_servers']['linear'])
assert linear.pop('tools') == {'include':['get_*','list_*','search_*']}
assert linear == original['mcp_servers']['linear']
assert value['plugins']['enabled']==['another-plugin','council-workspace']
assert not (home/'config.yaml').stat().st_mode & 0o077
tools=[]
register(SimpleNamespace(register_tool=lambda **fields: tools.append(fields)))
assert len(tools)==1 and tools[0]['name']=='council_tasks' and tools[0]['check_fn']()
(home/'.council/tasks-client.json').unlink()
assert not tools[0]['check_fn']()
print('Native task setup and plugin isolation verified')
"""
            result = subprocess.run(
                [sys.executable, "-c", program],
                env={**os.environ, "HERMES_HOME": directory, "HOME": directory},
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-1000:] + result.stderr[-2500:])
