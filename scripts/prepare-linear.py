#!/usr/bin/env python3
"""Prepare the native Linear connection disabled; OAuth remains in the dashboard."""

import json
import os
import tempfile
from pathlib import Path

URL = "https://mcp.linear.app/mcp"


def main():
    from hermes_cli.mcp_catalog import card_install_config, get_entry
    from hermes_constants import get_default_hermes_root
    from ruamel.yaml import YAML

    home = Path(os.environ["HERMES_HOME"])
    path = home / "config.yaml"
    if (
        os.geteuid() == 0
        or home.resolve() != get_default_hermes_root().resolve()
        or path.resolve() != path
        or not path.is_file()
    ):
        raise ValueError("Prepare Linear only in the coordinator's regular profile as its user.")
    entry = get_entry("linear")
    if (
        entry is None
        or entry.transport.type != "http"
        or entry.transport.url != URL
        or entry.auth.type != "oauth"
        or entry.install is not None
    ):
        raise ValueError("The pinned official Linear catalog entry is unavailable or incompatible.")
    yaml = YAML()
    config = yaml.load(path.read_text())
    if not isinstance(config, dict):
        raise ValueError("A coordinator configuration mapping is required.")
    servers = config.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("MCP server settings must be a mapping.")
    previous = servers.get("linear")
    if previous is not None:
        if (
            not isinstance(previous, dict)
            or previous.get("url") != URL
            or previous.get("auth") != "oauth"
        ):
            raise ValueError("Reconcile the existing Linear connection before replacing it.")
        print(json.dumps({"linear_prepared": True, "created": False, "changed": False}))
        return
    server = card_install_config(entry)
    server.update(enabled=False, tools={"include": []})
    servers["linear"] = server
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=home, prefix=".linear-config-", delete=False
        ) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            yaml.dump(config, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "linear_prepared": True,
                "created": True,
                "enabled": False,
                "selected_tools": 0,
                "authorization_required": True,
                "credentials_copied": False,
            }
        )
    )


if __name__ == "__main__":
    main()
