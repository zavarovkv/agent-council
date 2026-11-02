"""Checked activation of shared cloud tools."""

from .profile_setup import configure as provision


def configure(runtime):
    probe = """
import json
from pathlib import Path
from council_workspace.client import call
home = Path('/opt/data')
profiles = [home] + [home / 'profiles' / role for role in ('dexter', 'lando', 'padme')]
profiles += [home / '.council/linear' / role for role in ('qui-gon', 'dexter', 'marketing', 'padme')]
for profile in profiles:
    result = json.loads(call({'action': 'context'}, home=profile))
    assert 'notion_root' in result and 'drive_root' in result, 'Workspace listener unavailable'
print('Shared workspace: eight authenticated profiles ready')
"""
    return provision(
        runtime,
        capability="workspace-access-v1",
        script="provision-workspace.py",
        result_key="workspace_configured",
        probe=probe,
    )
