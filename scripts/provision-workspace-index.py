#!/usr/bin/env python3
"""Install one daily native cron job; a copied profile is checked before cutover."""

import json
import os
from pathlib import Path


def configure(home):
    from cron import jobs

    from council_recurring.client import atomic_json
    from council_workspace.policy import server_policy

    home = Path(home)
    if home.resolve() != home or os.geteuid() == 0:
        raise ValueError("Use the canonical profile as its runtime user.")
    policy = server_policy(home)
    script = home / "scripts/council-workspace-index.py"
    if script.resolve() != script:
        raise ValueError("Invalid index script path.")
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import sys\nsys.path.insert(0, '/opt/hermes')\nfrom council_workspace.index import main\nmain()\n"
    )
    script.chmod(0o600)
    name = "council-workspace-index"
    found = [j for j in jobs.list_jobs(include_disabled=True) if j.get("name") == name]
    if len(found) > 1 or (
        found and (found[0].get("fire_claim") or found[0].get("script") != script.name)
    ):
        raise ValueError("Reconcile the existing index job before provisioning.")
    fields = {
        "name": name,
        "prompt": "Refresh CouncilBase navigation daily at 06:30 Europe/Moscow. No LLM.",
        "schedule": "30 3 * * *",
        "deliver": "local",
        "failure_deliver": "local",
        "script": script.name,
        "interpreter": "/opt/hermes/.venv/bin/python",
        "no_agent": True,
        "skills": [],
        "context_from": [],
    }
    job = jobs.update_job(found[0]["id"], fields) if found else jobs.create_job(**fields)
    path = home / ".council/workspace/index-config.json"
    atomic_json(
        path,
        {
            "schema": 1,
            "enabled": True,
            "notion_root": policy["notion_root"],
            "drive_root": policy["drive_root"],
            "job": job["id"],
        },
    )
    return {"workspace_index_configured": True, "timezone": "Europe/Moscow", "daily_time": "06:30"}


if __name__ == "__main__":
    os.umask(0o077)
    print(json.dumps(configure(Path(os.environ["HERMES_HOME"]))))
