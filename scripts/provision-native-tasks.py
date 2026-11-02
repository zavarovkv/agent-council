#!/usr/bin/env python3
"""Enable app-owned task routing in the trusted owner profile; preserve OAuth grants."""

import io
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/opt/agent-council")


def configure(home):
    from ruamel.yaml import YAML

    from council_recurring.client import atomic_json
    from council_tasks import settings
    from linear_agent.config import Registry, private_json

    home = Path(home)
    if home.resolve() != home or os.geteuid() == 0:
        raise ValueError("Configure a canonical profile as its runtime user.")
    bridge = home / ".council/linear/bridge"
    registry = Registry.load(bridge)
    if len(registry.teams) != 1:
        raise ValueError("Select one native task team explicitly.")
    path = bridge / "native-tasks.json"
    previous = private_json(path) if path.exists() else None
    grant = {
        "schema": 1,
        "key": previous["key"] if previous else secrets.token_hex(32),
        "owner": registry.owner,
        "team": next(iter(registry.teams)),
        "project": None,
    }
    if previous:
        grant["project"] = previous["project"]
        if grant != previous:
            raise PermissionError("Reconcile changed native task owner and destinations.")
    client = {"schema": 1, "key": grant["key"]}
    client_path = home / ".council/tasks-client.json"
    if client_path.exists() and settings(home) != client:
        raise PermissionError("Preserve the existing native task grant.")
    yaml = YAML()
    config_path = home / "config.yaml"
    if config_path.resolve() != config_path:
        raise ValueError("Refusing a linked profile configuration.")
    config = yaml.load(config_path.read_text())
    plugins = config.setdefault("plugins", {})
    if "council-workspace" in plugins.get("disabled", []):
        raise ValueError("The native task plugin is explicitly disabled.")
    if "council-workspace" not in plugins.setdefault("enabled", []):
        plugins["enabled"].append("council-workspace")
    linear = config.get("mcp_servers", {}).get("linear")
    if linear:
        # The personal OAuth connection remains available for reading. Mutations
        # must use the app-owned coordinator path, not the owner's identity.
        linear["tools"] = {"include": ["get_*", "list_*", "search_*"]}
    output = io.StringIO()
    yaml.dump(config, output)
    atomic_json(path, grant)
    atomic_json(client_path, client)
    with tempfile.NamedTemporaryFile(dir=home, delete=False) as temporary:
        os.fchmod(temporary.fileno(), 0o600)
        temporary.write(output.getvalue().encode())
        temporary.flush()
        os.fsync(temporary.fileno())
    os.replace(temporary.name, config_path)
    settings(home)
    return {"native_tasks_configured": True, "personal_linear_read_only": bool(linear)}


if __name__ == "__main__":
    os.umask(0o077)
    print(json.dumps(configure(Path(os.environ["HERMES_HOME"]))))
