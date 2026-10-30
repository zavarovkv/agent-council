#!/usr/bin/env python3
"""Enable native research tracking/filing only in the trusted coordinator profile."""

import json
import os
import tempfile
import uuid
from pathlib import Path


def main():
    from hermes_cli import kanban_db as kb
    from hermes_constants import get_default_hermes_root
    from ruamel.yaml import YAML

    home = Path(os.environ["HERMES_HOME"])
    if os.geteuid() == 0 or home.resolve() != get_default_hermes_root().resolve():
        raise ValueError("Configure research only as the coordinator profile user.")
    workspace = home / "council-workspace.json"
    if workspace.resolve() != workspace or not workspace.is_file():
        raise ValueError("Configure the existing research workspace first.")
    knowledge = json.loads(workspace.read_text()).get("knowledge_base", {})
    if knowledge.get("provider") != "notion":
        raise ValueError("The reviewed research destination must be Notion.")
    uuid.UUID(knowledge.get("research", {}).get("page_id", ""))
    path = home / "config.yaml"
    if path.resolve() != path or not path.is_file():
        raise ValueError("A regular coordinator configuration is required.")
    yaml = YAML()
    config = yaml.load(path.read_text()) or {}
    plugins = config.setdefault("plugins", {})
    if "council-research" in (plugins.get("disabled") or []):
        raise ValueError("The research plugin is explicitly disabled; reconcile that policy first.")
    enabled = plugins.setdefault("enabled", [])
    if not isinstance(enabled, list):
        raise ValueError("plugins.enabled must be a list.")
    if "council-research" not in enabled:
        enabled.append("council-research")
    entry = plugins.setdefault("entries", {}).setdefault("council-research", {})
    settings = entry.setdefault("settings", {})
    if settings.get("board", "research") != "research":
        raise ValueError("Reconcile the existing research board before reconfiguration.")
    settings.update(enabled=True, board="research")
    allowlist = entry.setdefault("mcp_allowlist", [])
    if not isinstance(allowlist, list):
        raise ValueError("Invalid existing research MCP allowlist.")
    if "notion" not in allowlist:
        allowlist.append("notion")
    if not kb.board_exists("research"):
        kb.create_board(
            "research",
            name="Исследования",
            description="Задачи Dexter Jettster, проверка и результаты исследований",
        )
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=home, prefix=".research-config-", delete=False
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
                "research_configured": True,
                "board": "research",
                "coordinator_only": True,
                "credentials_copied": False,
            }
        )
    )


if __name__ == "__main__":
    main()
