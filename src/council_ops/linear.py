"""Prepare, configure and enable owner-bound Linear agents without cloning credentials."""

import getpass
import json
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

from linear_agent.config import Registry, app_manifest, identifier, private_json

from .config import OperationError, atomic_write
from .instructions import verify_capabilities
from .lifecycle import identity, smoke
from .linear_state import root
from .snapshots import snapshot


def application_links(plan):
    return {
        slug: "https://linear.app/settings/api/applications/new?"
        + urlencode(
            {
                "manifest": json.dumps(
                    app_manifest(plan["origin"], slug), ensure_ascii=False, separators=(",", ":")
                )
            }
        )
        for slug in plan["agents"]
    }


def prepare_files(home, plan, model, repository, uid, gid):
    directory = home / ".council/linear"
    marker = directory / ".managed"
    if directory.resolve() != directory or (
        directory.exists() and any(directory.iterdir()) and not marker.is_file()
    ):
        raise OperationError("Refusing an unmanaged or symlinked Linear profile.")
    bridge = directory / "bridge"
    bridge.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous = bridge / "plan.json"
    if previous.exists() and private_json(previous) != plan:
        raise OperationError(
            "Review Linear identity/team changes explicitly; existing setup is preserved."
        )
    atomic_write(previous, (json.dumps(plan) + "\n").encode())
    atomic_write(marker, b'{"schema":1,"managed_by":"agent-council"}\n')
    for slug in plan["agents"]:
        profile = directory / slug
        config = profile / "config.yaml"
        if profile.resolve() != profile:
            raise OperationError("Refusing a symlinked executor profile.")
        profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in ("workspace", "home"):
            (profile / name).mkdir(mode=0o700, exist_ok=True)
        if not config.exists():
            source = {
                "model": model,
                "platform_toolsets": {"api_server": ["web", "clarify", "council-linear"]},
                "mcp_servers": {},
                "plugins": {"enabled": ["council-linear"]},
                "security": {"allow_lazy_installs": False},
                "agent": {"max_turns": 20},
                "gateway": {"api_server": {"max_concurrent_runs": 1}},
                "session": {"retention_days": 14},
            }
            atomic_write(config, (json.dumps(source, indent=2) + "\n").encode())
            atomic_write(
                profile / "SOUL.md",
                (repository / "instructions/linear" / slug / "SOUL.md").read_bytes(),
            )
        environment = bridge / (slug + ".env")
        if not environment.exists():
            atomic_write(
                environment,
                (
                    "API_SERVER_KEY="
                    + secrets.token_hex(32)
                    + "\nCOUNCIL_LINEAR_AGENT="
                    + slug
                    + "\n"
                ).encode(),
            )
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in [".", *directories, *files]:
            target = Path(parent) / name
            if target.is_symlink():
                raise OperationError("Refusing symlinked Linear installation data.")
            os.chown(target, uid, gid, follow_symlinks=False)


def prepare(runtime, *, owner, organization, teams, with_dexter=False):
    verify_capabilities(runtime, ["linear-agents-v1"])
    plan = {
        "schema": 1,
        "owner": identifier(owner),
        "organization": identifier(organization),
        "teams": sorted({identifier(team) for team in teams}),
        "origin": runtime.settings.values["HERMES_PUBLIC_URL"].rstrip("/"),
        "agents": ["qui-gon", "dexter"] if with_dexter else ["qui-gon"],
    }
    if not plan["teams"]:
        raise OperationError("Select at least one explicit Linear team.")
    source = "import json,os; from pathlib import Path; from hermes_cli.config import read_user_config_raw; m=read_user_config_raw(Path(os.environ['HERMES_HOME'])/'config.yaml').get('model',{}); print(json.dumps({k:m.get(k,'') for k in ('provider','default')}))"
    model = json.loads(
        runtime.compose(
            "exec", "-T", "--user", "hermes", "hermes", "/opt/hermes/.venv/bin/python", "-c", source
        )
    )
    if not all(isinstance(model.get(key), str) and model[key] for key in ("provider", "default")):
        raise OperationError("Select the coordinator's public model choice first.")
    uid, gid = identity(runtime.settings)
    checkpoint = snapshot(runtime)

    def create(home):
        prepare_files(home, plan, model, runtime.settings.repository, uid, gid)

    smoke(runtime, checkpoint, prepare_copy=create)
    create(runtime.settings.data_dir)
    runtime.settings.update_values(
        {
            "COUNCIL_LINEAR_ENABLED": "true",
            "COUNCIL_LINEAR_DEXTER_ENABLED": "true" if with_dexter else "false",
        }
    )
    try:
        runtime.start()
        runtime.ready()
    except Exception:
        # Never restore/restart unrelated services when optional setup fails.
        runtime.compose(
            "stop",
            "linear-adapter",
            "linear-executor",
            *(["dexter-executor"] if with_dexter else []),
        )
        runtime.settings.update_values(
            {"COUNCIL_LINEAR_ENABLED": "false", "COUNCIL_LINEAR_DEXTER_ENABLED": "false"}
        )
        raise OperationError(
            "Optional Linear setup failed; only Linear services were stopped. Profiles and backup were retained."
        ) from None
    return {
        "prepared": True,
        "execution_enabled": False,
        "model_credentials_copied": False,
        "backup": str(checkpoint),
        "create_applications": application_links(plan),
        "next_step": "configure-linear-agents, own model login, OAuth app installation, linear-execution --enable",
    }


def configure(runtime):
    if not sys.stdin.isatty():
        raise OperationError(
            "Enter Linear application credentials through this script in a private interactive terminal."
        )
    directory = root(runtime.settings) / "bridge"
    plan = private_json(directory / "plan.json")
    old = private_json(directory / "agents.json") if (directory / "agents.json").exists() else None
    if old and old.get("enabled"):
        raise OperationError("Disable Linear execution before changing application credentials.")
    from .config import read_settings

    records = {}
    for slug in plan["agents"]:
        previous = (old or {}).get("agents", {}).get(slug, {})
        values = {}
        for name, label in (
            ("client_id", "Client ID"),
            ("client_secret", "Client secret"),
            ("webhook_secret", "Webhook signing secret"),
        ):
            value = getpass.getpass(
                slug + " — " + label + (" (Enter keeps existing): " if previous.get(name) else ": ")
            )
            values[name] = value or previous.get(name, "")
        if previous.get("client_id") and previous["client_id"] != values["client_id"]:
            raise OperationError(
                "Changing an existing application's identity requires a separately reviewed migration; its grant is preserved."
            )
        key = read_settings(directory / (slug + ".env"))["API_SERVER_KEY"]
        records[slug] = {
            **values,
            "executor_url": "http://"
            + ("linear-executor" if slug == "qui-gon" else "dexter-executor")
            + ":8642",
            "executor_key": key,
            "delegates": ["dexter"] if slug == "qui-gon" and "dexter" in plan["agents"] else [],
        }
    value = {
        "schema": 1,
        "enabled": False,
        "owner": plan["owner"],
        "organization": plan["organization"],
        "teams": plan["teams"],
        "origin": plan["origin"],
        "revision": secrets.token_hex(16),
        "admission_since": time.time(),
        "agents": records,
    }
    # Validate credentials on a copied profile before changing live configuration.
    checkpoint = snapshot(runtime)
    uid, gid = identity(runtime.settings)

    def write(home):
        path = home / ".council/linear/bridge/agents.json"
        atomic_write(path, (json.dumps(value) + "\n").encode())
        os.chown(path, uid, gid)
        Registry.load(path.parent)

    smoke(runtime, checkpoint, prepare_copy=write)
    write(runtime.settings.data_dir)
    return {
        "configured": True,
        "execution_enabled": False,
        "credentials_printed": False,
        "backup": str(checkpoint),
    }


def setup_window(runtime):
    directory = root(runtime.settings) / "bridge"
    registry = Registry.load(directory)
    path = directory / "setup.json"
    atomic_write(path, (json.dumps({"expires": time.time() + 900}) + "\n").encode())
    os.chown(path, *identity(runtime.settings))
    return {
        "setup_window_minutes": 15,
        "connect": {
            slug: registry.origin + f"/integrations/linear/{slug}/oauth/authorize"
            for slug in registry.agents
        },
    }


def execution(runtime, *, enabled):
    directory = root(runtime.settings) / "bridge"
    current = private_json(directory / "agents.json")
    registry = Registry.load(directory)
    checkpoint = None
    if enabled:
        from linear_agent.store import Store

        verify_capabilities(runtime, ["linear-agents-v1"])
        if not (directory / "linear.sqlite3").is_file():
            raise OperationError("Start the isolated adapter and complete installation first.")
        store = Store(directory)
        app_users = set()
        for slug in registry.agents:
            installed = store.installation(slug)
            if not installed or installed["organization"] != registry.organization:
                raise OperationError("Install every configured Linear application first.")
            if installed["app_user"] in app_users or installed["app_user"] == registry.owner:
                raise OperationError("Each visible agent requires its own app identity.")
            app_users.add(installed["app_user"])
            service = "linear-executor" if slug == "qui-gon" else "dexter-executor"
            probe = "import json,os; from pathlib import Path; from hermes_cli.config import read_user_config_raw; from hermes_cli.auth import get_auth_status; p=read_user_config_raw(Path(os.environ['HERMES_HOME'])/'config.yaml')['model']['provider']; print(json.dumps({'logged_in':get_auth_status(p).get('logged_in') is True}))"
            status = json.loads(
                runtime.compose("exec", "-T", service, "/opt/hermes/.venv/bin/python", "-c", probe)
            )
            if status.get("logged_in") is not True:
                raise OperationError("Authorize each isolated executor's own model account first.")
        checkpoint = snapshot(runtime)
        smoke(runtime, checkpoint)
    value = {
        **current,
        "enabled": enabled,
        "revision": secrets.token_hex(16),
        "admission_since": time.time(),
    }
    path = directory / "agents.json"
    atomic_write(path, (json.dumps(value) + "\n").encode())
    os.chown(path, *identity(runtime.settings))
    result = {"enabled": enabled, "owner_only": True, "restart_required": False}
    if checkpoint:
        result["backup"] = str(checkpoint)
    return result
