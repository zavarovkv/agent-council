#!/usr/bin/env python3
"""Create one pinned main-profile scheduling grant; never print its key."""

import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, "/opt/agent-council")


def configure(home):
    from council_recurring.client import atomic_json, settings
    from linear_agent.config import Registry, private_json

    home = Path(home)
    if home.resolve() != home or os.geteuid() == 0:
        raise ValueError("Configure a canonical profile as its runtime user.")
    bridge = home / ".council/linear/bridge"
    registry = Registry.load(bridge)
    owner_path = home / "telegram-owner.json"
    if owner_path.resolve() != owner_path:
        raise ValueError("Invalid owner pin.")
    owner = json.loads(owner_path.read_text())["telegram_id"]
    if type(owner) is not int or not 0 < owner < 2**63 or len(registry.teams) != 1:
        raise ValueError("Select one owner and team before enabling recurring work.")
    workspace = json.loads((home / "council-workspace.json").read_text())
    parent = workspace["knowledge_base"]["research"]["page_id"].replace("-", "")
    path = bridge / "recurring.json"
    grant = (
        private_json(path)
        if path.exists()
        else {"schema": 1, "key": secrets.token_hex(32), "owner": registry.owner}
    )
    if grant["owner"] != registry.owner:
        raise PermissionError("Preserve the existing scheduling owner grant.")
    proposed = {
        "schema": 1,
        "key": grant["key"],
        "team": next(iter(registry.teams)),
        "project": None,
        "notion_parent": parent,
        "delivery": "telegram:" + str(owner),
    }
    client_path = home / ".council/recurring/client.json"
    if client_path.exists():
        previous = settings(home)
        proposed["project"] = previous["project"]
        if previous != proposed:
            raise PermissionError("Reconcile changed scheduling destinations and owner pins.")
    atomic_json(path, grant)
    atomic_json(client_path, proposed)
    settings(home)
    return {"recurring_configured": True}


if __name__ == "__main__":
    os.umask(0o077)
    print(json.dumps(configure(Path(os.environ["HERMES_HOME"]))))
