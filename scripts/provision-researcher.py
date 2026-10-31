#!/usr/bin/env python3
"""Provision the internal Dexter profile through Hermes' native profile API.

Run explicitly as the Hermes user, with the gateway stopped. Runtime state stays
in HERMES_HOME; this installer and the role definitions are immutable image files.
"""

import copy
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

PROFILE = "dexter"
PLUGIN = "advanced-delegation"
DESCRIPTION = "Декстер Джеттстер: public competitor and market research with sources."
MARKER = ".council-researcher.json"


def digest(content):
    return hashlib.sha256(content).hexdigest()


def configure_coordinator(original):
    """Merge one explicit route, preserving unrelated settings and credentials."""
    config = copy.deepcopy(original)
    plugins = config.setdefault("plugins", {})
    if PLUGIN in (plugins.get("disabled") or []):
        raise ValueError("The delegation plugin is explicitly disabled; resolve that policy first.")
    enabled = plugins.setdefault("enabled", [])
    if not isinstance(enabled, list):
        raise ValueError("plugins.enabled must be a list.")
    if PLUGIN not in enabled:
        enabled.append(PLUGIN)
    delegation = config.setdefault("delegation", {})
    if delegation.get("backend") not in (None, "", "default", "profile_routes"):
        raise ValueError("An existing delegation backend would be replaced; resolve it first.")
    delegation["backend"] = "profile_routes"
    routes = delegation.setdefault("backends", {}).setdefault("profile_routes", {})
    profiles = routes.setdefault("profiles", [])
    if not isinstance(profiles, list) or not all(isinstance(p, dict) for p in profiles):
        raise ValueError("profile_routes.profiles must be a list of mappings.")
    matches = [p for p in profiles if p.get("name") == PROFILE]
    if len(matches) > 1:
        raise ValueError("Duplicate Dexter routes must be resolved first.")
    if matches:
        matches[0]["description"] = DESCRIPTION
    else:
        profiles.append({"name": PROFILE, "description": DESCRIPTION})
    return config


def researcher_config(original, coordinator, workspace):
    config = copy.deepcopy(original)
    model = coordinator.get("model", {})
    if model.get("provider") != "openai-codex" or not model.get("default"):
        raise ValueError(
            "Configure the coordinator's OpenAI Codex model before provisioning Dexter."
        )
    # Credentials are deliberately not cloned. Hermes' credential pool falls
    # back to the root Codex grant, including its locked refresh/write-through.
    config["model"] = {"provider": "openai-codex", "default": model["default"]}
    reasoning = coordinator.get("agent", {}).get("reasoning_effort")
    if reasoning is not None:
        config.setdefault("agent", {})["reasoning_effort"] = reasoning
    config.setdefault("terminal", {}).update({"backend": "local", "cwd": str(workspace)})
    config.setdefault("agent", {}).setdefault("max_turns", 30)
    return config


def atomic_private_write(path, content):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".council-", delete=False) as out:
            temporary = Path(out.name)
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def require_regular(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Refusing a symlink or non-file: {path.name}")


def provision(home, assets, read_config, write_config, create_profile):
    """Install managed instructions idempotently without overwriting user edits."""
    home = home.resolve(strict=True)
    profile = home / "profiles" / PROFILE
    skill = home / "skills" / "competitor-research" / "SKILL.md"
    marker = profile / MARKER
    config_path = home / "config.yaml"
    require_regular(config_path)
    initial_config = config_path.read_bytes()
    coordinator = read_config(config_path)
    desired_coordinator = configure_coordinator(coordinator)
    # Validate the model before creating any state.
    researcher_config({}, coordinator, profile / "research")
    soul = (assets / PROFILE / "SOUL.md").read_bytes()
    workflow = (assets / "assistant" / "skills" / "competitor-research" / "SKILL.md").read_bytes()
    for parent in (home / "profiles", home / "skills", skill.parent, profile):
        if parent.is_symlink():
            raise ValueError(f"Refusing a symlinked managed directory: {parent.name}")
    for path in (marker, skill, profile / "SOUL.md", profile / "config.yaml"):
        require_regular(path)
    if profile.exists():
        if not marker.is_file():
            raise ValueError("Dexter already exists and is not managed by this installer.")
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if previous.get("schema") != 1 or previous.get("profile") != PROFILE:
            raise ValueError("Invalid researcher ownership marker.")
        for path, key in ((profile / "SOUL.md", "soul"), (skill, "workflow")):
            if not path.is_file() or digest(path.read_bytes()) != previous.get(key):
                raise ValueError(
                    f"Managed instructions were edited; preserve and reconcile {path.name}."
                )
    else:
        if skill.exists():
            raise ValueError("An unmanaged competitor-research skill already exists.")
        created = create_profile(
            PROFILE,
            no_alias=True,
            no_skills=True,
            description=DESCRIPTION,
            soul=soul.decode("utf-8"),
        )
        if created.resolve() != profile:
            raise ValueError(
                "Hermes resolved a different profile root; coordinator was not changed."
            )
    # Backups remain private and outside the checkout. A rerun never replaces
    # the original rollback configuration or any reports, memory or credentials.
    backup = home / "config.yaml.before-dexter"
    require_regular(backup)
    if not backup.exists():
        atomic_private_write(backup, initial_config)
    workspace = profile / "research"
    if workspace.is_symlink():
        raise ValueError("Refusing a symlinked research workspace.")
    workspace.mkdir(mode=0o700, exist_ok=True)
    child_path = profile / "config.yaml"
    child = read_config(child_path)
    write_config(child_path, researcher_config(child, coordinator, workspace))
    atomic_private_write(profile / "SOUL.md", soul)
    atomic_private_write(skill, workflow)
    atomic_private_write(
        marker,
        (
            json.dumps(
                {
                    "schema": 1,
                    "profile": PROFILE,
                    "soul": digest(soul),
                    "workflow": digest(workflow),
                },
                indent=2,
            )
            + "\n"
        ).encode(),
    )
    if config_path.read_bytes() != initial_config:
        raise ValueError("Coordinator settings changed concurrently; stop the gateway and retry.")
    write_config(config_path, desired_coordinator)
    return profile


def main():
    if os.geteuid() == 0:
        raise SystemExit("Run with docker exec --user hermes; root is not supported.")
    from hermes_cli.config import atomic_config_replace, read_user_config_raw
    from hermes_cli.profiles import create_profile
    from hermes_constants import get_hermes_home

    home = Path(get_hermes_home())
    with (home / ".council-researcher.lock").open("a", encoding="utf-8") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        profile = provision(
            home,
            Path(__file__).parent / "instructions",
            read_user_config_raw,
            atomic_config_replace,
            create_profile,
        )
    print(f"Dexter is configured at {profile}; restart only the coordinator gateway.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f"Researcher setup refused: {exc}", file=sys.stderr)
        sys.exit(1)
