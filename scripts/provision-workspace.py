#!/usr/bin/env python3
"""Grant all four trusted agents cloud workspace tools without copying OAuth grants."""

import io
import json
import os
import secrets
import tempfile
from pathlib import Path


def write(path, data):
    if path.resolve() != path:
        raise ValueError("Refusing a symlinked workspace configuration.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(output.name, path)


def configure(home):
    from ruamel.yaml import YAML

    from council_workspace.policy import server_policy
    from research_filing.notion import page_id

    home = Path(home)
    if home.resolve() != home or os.geteuid() == 0:
        raise ValueError("Configure a canonical profile as its runtime user.")
    workspace = json.loads((home / "council-workspace.json").read_text())
    knowledge, files = workspace["knowledge_base"], workspace["files"]
    if knowledge["provider"] != "notion" or files["provider"] != "google_drive":
        raise ValueError("Prepare the existing Notion and Drive workspaces first.")
    policy_path = home / ".council/workspace/access.json"
    previous = server_policy(home) if policy_path.exists() else {"clients": {}}
    policy = {
        "schema": 1,
        "enabled": True,
        "clients": {},
        "notion_root": page_id(knowledge["page_id"]),
        "drive_root": files["folder_id"],
    }
    staged = {}
    yaml = YAML()
    for role, profile in (
        ("qui-gon", home),
        ("dexter", home / "profiles/dexter"),
        ("lando", home / "profiles/lando"),
        ("padme", home / "profiles/padme"),
    ):
        for mode, target in (
            ("native", profile),
            ("linear", home / ".council/linear" / ("marketing" if role == "lando" else role)),
        ):
            if target.resolve() != target or not (target / "config.yaml").is_file():
                raise ValueError("Prepare all four native and Linear profiles first.")
            identity = mode + "-" + role
            grant = previous["clients"].get(identity) or {
                "role": role,
                "key": secrets.token_hex(32),
            }
            policy["clients"][identity] = grant
            staged[target / "workspace-client.json"] = json.dumps(
                {"schema": 1, "key": grant["key"]}
            ).encode()
            if mode == "native":
                config = yaml.load((target / "config.yaml").read_text())
                plugins = config.setdefault("plugins", {})
                if "council-workspace" in plugins.get("disabled", []):
                    raise ValueError("Reconcile the explicitly disabled workspace plugin first.")
                enabled = plugins.setdefault("enabled", [])
                if "council-workspace" not in enabled:
                    enabled.append("council-workspace")
                for names in config.get("platform_toolsets", {}).values():
                    if isinstance(names, list) and "council_workspace" not in names:
                        names.append("council_workspace")
                buffer = io.StringIO()
                yaml.dump(config, buffer)
                staged[target / "config.yaml"] = buffer.getvalue().encode()
    hook = home / "hooks/council-workspace"
    staged[hook / "HOOK.yaml"] = (
        b"name: council-workspace\ndescription: Start the internal shared cloud workspace\nevents: [gateway:startup]\n"
    )
    staged[hook / "handler.py"] = (
        b"def handle(event_type, context):\n    from council_workspace.server import start\n    start()\n"
    )
    staged[policy_path] = (json.dumps(policy, indent=2) + "\n").encode()
    for path, data in staged.items():
        write(path, data)
    server_policy(home)
    print(
        json.dumps(
            {
                "workspace_configured": True,
                "native_agents": 4,
                "linear_agents": 4,
                "oauth_grants_copied": False,
            }
        )
    )


if __name__ == "__main__":
    configure(Path(os.environ["HERMES_HOME"]))
