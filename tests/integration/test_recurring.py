"""Exercise the real pinned native cron store, interpreter and execution ledger offline."""

import os
import subprocess
import sys
import tempfile
import unittest


class RecurringRuntimeTests(unittest.TestCase):
    def test_native_schedule_management_and_no_agent_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            program = r"""
from pathlib import Path
import json
from datetime import datetime
from council_recurring.client import configure, atomic_json, manifest
from council_recurring.contract import revision
from council_recurring.runner import execution_for
from cron import jobs, executions
from cron.scheduler import _run_no_agent_job
home=Path(__import__('os').environ['HERMES_HOME'])
atomic_json(home/'.council/recurring/client.json', {'schema':1,'key':'a'*64,'team':'1'*32,'project':None,'notion_parent':'2'*32,'delivery':'telegram:12345'})
value={'id':'fixture-report','title':'Fixture','brief':'Offline test','team':'1'*32,'project':None,'notion_parent':'2'*32,'start_time':'07:20','cutoff_time':'07:15','delivery_time':'08:00','weekdays':list(range(7)),'window_days':1,'telegram_source':False}
def remote(_home, payload):
    assert payload['action'] == 'configure'
    return {'definition':payload['definition'],'revision':revision(payload['definition'])}
legacy_start=jobs.create_job(prompt='legacy preparation',schedule='20 4 * * *',name='old-travel-preparation')['id']
legacy_delivery=jobs.create_job(prompt='legacy delivery',schedule='0 5 * * 1-5',name='old-travel-delivery')['id']
first=configure(home,value,native=jobs,remote=remote,adopt={'start':legacy_start,'delivery':legacy_delivery})
assert first['jobs']['start']['id']==legacy_start and first['jobs']['delivery']['id']==legacy_delivery
second=configure(home,{**value,'delivery_time':'08:30'},native=jobs,remote=remote)
assert {k:j['id'] for k,j in first['jobs'].items()} == {k:j['id'] for k,j in second['jobs'].items()}
assert len(jobs.list_jobs(include_disabled=True))==2
for identity in manifest(home)['fixture-report']['jobs'].values():
    job=jobs.get_job(identity)
    assert job['no_agent'] and job['interpreter']=='/opt/hermes/.venv/bin/python'
    assert job['failure_deliver']=='local'
    assert (home/'scripts'/job['script']).is_file()
job=jobs.get_job(second['jobs']['start']['id'])
execution=executions.create_execution(job['id'],source='manual')
identity=execution['id']
executions.mark_execution_running(identity)
assert execution_for(job['id'],executions)['id']==identity
# The actual subprocess runs with the pinned interpreter and the correct profile.
probe=home/'scripts/offline-probe.py'
probe.write_text("from council_recurring.client import home_path\nfrom pathlib import Path\nassert home_path()==Path("+repr(str(home))+")\nprint('Verified script execution')\n")
job['script']=probe.name
ok,document,output,error=_run_no_agent_job(job,job['id'],'fixture',None)
assert ok and output.strip()=='Verified script execution', (ok,output,error)
executions.finish_execution(identity,success=True,delivery_outcome='delivered')
assert executions.get_execution(identity)['status']=='completed'
import importlib.util
atomic_json(home/'.council/workspace/access.json', {'schema':1,'enabled':True,'notion_root':'1'*32,'drive_root':'folder-fixture','clients':{'native-qui-gon':{'role':'qui-gon','key':'e'*64}}})
spec=importlib.util.spec_from_file_location('index_setup','/opt/agent-council/provision-workspace-index.py')
setup=importlib.util.module_from_spec(spec); spec.loader.exec_module(setup)
assert setup.configure(home)['daily_time']=='06:30'
assert setup.configure(home)['workspace_index_configured']
indices=[j for j in jobs.list_jobs(include_disabled=True) if j.get('name')=='council-workspace-index']
assert len(indices)==1 and indices[0]['no_agent']
assert indices[0]['schedule']['expr']=='30 3 * * *', indices[0]['schedule']
print('Native cron contract verified')
"""
            result = subprocess.run(
                [sys.executable, "-c", program],
                env={**os.environ, "HERMES_HOME": directory, "HOME": directory},
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-1000:] + result.stderr[-2500:])
