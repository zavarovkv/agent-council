"""Read-only checks of the actual Docker, process and secret-file boundaries."""

import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .config import OperationError


def unit_status(runtime, name):
    try:
        value = runtime.run(
            [
                "systemctl",
                "show",
                name,
                "--property=ActiveState,UnitFileState,Result,ExecMainStatus",
            ]
        )
        values = dict(line.split("=", 1) for line in value.splitlines() if "=" in line)
        return {
            key: text
            for key, text in values.items()
            if key in {"ActiveState", "UnitFileState", "Result", "ExecMainStatus"}
            and re.fullmatch(r"[a-z0-9-]{1,40}", text)
        }
    except OperationError:
        return {"available": False}


def instruction_status(home):
    from .instructions import MARKER, destination, digest, previous_files

    path = home / MARKER
    if not path.exists() and not path.is_symlink():
        return {"configured": False}
    try:
        files = previous_files(home)
        revision = json.loads(path.read_text())["revision"]
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("Invalid revision")
        changed = False
        for name, expected in files.items():
            item = destination(home, name)
            if (
                not item.is_file()
                or item.stat().st_size > 1048576
                or digest(item.read_bytes()) != expected
            ):
                changed = True
        return {
            "configured": True,
            "available": True,
            "revision": revision,
            "files": len(files),
            "local_changes": changed,
        }
    except (OperationError, OSError, ValueError, KeyError, TypeError):
        return {"configured": True, "available": False}


def backup_status(settings):
    from .config import read_settings
    from .restic import validate_r2

    result = {"configured": False, "last_verified": None}
    try:
        for path in (
            settings.env_file.with_name("backup.env"),
            settings.env_file.with_name("backup-password"),
        ):
            metadata = path.lstat()
            if (
                path.is_symlink()
                or not path.is_file()
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077
            ):
                raise ValueError("Invalid private backup file")
        validate_r2(read_settings(settings.env_file.with_name("backup.env")))
        if settings.env_file.with_name("backup-password").stat().st_size < 24:
            raise ValueError("Invalid recovery password file")
        result["configured"] = True
        path = settings.env_file.with_name("last-cloud-backup.json")
        if path.is_symlink():
            raise ValueError("Invalid backup status file")
        if path.is_file():
            value = json.loads(path.read_text())
            timestamp = datetime.fromisoformat(value["verified_at"])
            if timestamp.tzinfo is None or not re.fullmatch(r"[a-f0-9]{64}", value["snapshot"]):
                raise ValueError("Invalid recovery verification record")
            result.update(
                last_verified=timestamp.isoformat(),
                snapshot=value["snapshot"],
                age_seconds=max(0, int((datetime.now(UTC) - timestamp).total_seconds())),
            )
    except (OperationError, OSError, ValueError, KeyError, TypeError):
        pass
    return result


def secretary_status(settings, inspected):
    """Report actual loaded policy and queue counts, never identities or message text.

    Optional-route faults are reported rather than preventing deploy/restore from
    repairing them. Container isolation failures below remain hard failures.
    """
    from telegram_secretary.guest_policy import load_state
    from telegram_secretary.policy import Settings as SecretarySettings

    environment = dict(
        item.split("=", 1) for item in inspected.get("Config", {}).get("Env", []) if "=" in item
    )
    if environment.get("COUNCIL_SECRETARY_ENABLED") != "true":
        return {"enabled": False}
    policy = SecretarySettings.load({**environment, "HERMES_HOME": str(settings.data_dir)})
    queues = {}
    guests = load_state(settings.data_dir)
    for route, enabled, filename in (
        ("business", policy.enabled, "queue.sqlite3"),
        ("public", policy.public_enabled, "public-queue.sqlite3"),
        ("guest", guests.enabled, "guest-queue.sqlite3"),
    ):
        if not enabled:
            queues[route] = {"enabled": False}
            continue
        path = settings.data_dir / "plugin-data/council-secretary" / filename
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("Missing queue")
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("Invalid queue")
                states = dict(db.execute("SELECT status,count(*) FROM jobs GROUP BY status"))
                oldest = db.execute(
                    "SELECT min(created) FROM jobs WHERE status IN ('queued','working','sending')"
                ).fetchone()[0]
            allowed = {
                "queued",
                "working",
                "sending",
                "sent",
                "failed",
                "cancelled",
                "uncertain",
                "expired",
            }
            if not set(states) <= allowed:
                raise ValueError("Invalid queue state")
            queues[route] = {
                "enabled": True,
                "available": True,
                "counts": states,
                "oldest_pending_age_seconds": max(0, int(time.time() - oldest))
                if oldest is not None
                else 0,
            }
        except (OSError, sqlite3.Error, ValueError):
            queues[route] = {"enabled": True, "available": False}
    return {
        "enabled": True,
        "policy_valid": policy.enabled,
        "queues": queues,
        "guest_policy_valid": not guests.error,
        "guest_owner_only": True,
    }


def generation_status(home):
    """Read aggregate reservations/outcomes without loading message bodies."""
    path = home / ".council/secretary/secretary-history.sqlite3"
    if not path.exists() and not path.is_symlink():
        return {"available": False}

    try:
        if path.resolve() != path or not path.is_file():
            raise ValueError("Invalid history path")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "generation_events" not in tables:
                return {"available": False}
            counts = dict(
                db.execute(
                    "SELECT route,count(*) FROM turns WHERE created>? GROUP BY route",
                    (time.time() - 3600,),
                )
            )
            outcomes = {}
            for route in ("business", "public"):
                row = db.execute(
                    "SELECT outcome,created FROM generation_events WHERE route=? ORDER BY created DESC,rowid DESC LIMIT 1",
                    (route,),
                ).fetchone()
                if row and row[0] in {
                    "ok",
                    "provider_auth",
                    "provider_quota",
                    "provider_timeout",
                    "model_unavailable",
                    "invalid_reply",
                }:
                    outcomes[route] = {"outcome": row[0], "at": row[1]}
        return {
            "available": True,
            "reserved_last_hour": {
                route: int(counts.get(route, 0)) for route in ("business", "public")
            },
            "last_outcomes": outcomes,
        }
    except (ValueError, OSError, sqlite3.Error):
        return {"available": False}


def research_status(settings):
    configured = settings.env_file.with_name("research.json").is_file()
    result = {"configured": configured}
    if not configured:
        return result
    path = settings.data_dir / ".council/research-filing/outbox.sqlite3"
    if not path.exists() and not path.is_symlink():
        return {**result, "available": True, "pending": 0, "oldest_pending_age_seconds": 0}
    try:
        if path.resolve() != path or not path.is_file():
            raise ValueError("Invalid outbox")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Invalid outbox")
            counts = dict(db.execute("SELECT state,count(*) FROM filings GROUP BY state"))
            oldest = db.execute("SELECT min(created) FROM filings WHERE state!='done'").fetchone()[
                0
            ]
        if not set(counts) <= {
            "queued",
            "creating",
            "writing",
            "indexing",
            "notion_verified",
            "done",
        }:
            raise ValueError("Invalid filing state")
        return {
            **result,
            "available": True,
            "counts": counts,
            "pending": sum(n for state, n in counts.items() if state != "done"),
            "oldest_pending_age_seconds": max(0, int(time.time() - oldest))
            if oldest is not None
            else 0,
        }
    except (OSError, ValueError, sqlite3.Error):
        return {**result, "available": False}


def doctor(runtime):
    from .linear_state import inspect_services

    runtime.compose("config", "--quiet")
    inspected = runtime.inspect()
    expected = {
        "/opt/data": runtime.settings.data_dir,
        "/workspace": runtime.settings.workspace_dir,
    }
    mounts = inspected["Mounts"]
    if len(mounts) != len(expected):
        raise OperationError("Unexpected runtime mounts; inspect the deployment privately.")
    for mount in mounts:
        if expected.get(mount["Destination"]) != Path(mount["Source"]) or not mount["RW"]:
            raise OperationError(
                "Runtime volume configuration differs from the deployment settings."
            )
    bindings = inspected["HostConfig"]["PortBindings"]
    if set(bindings) != {"9119/tcp"} or len(bindings["9119/tcp"]) != 1:
        raise OperationError("Unexpected published runtime ports.")
    binding = bindings["9119/tcp"][0]
    if binding["HostIp"] != "127.0.0.1" or binding["HostPort"] != runtime.settings.values.get(
        "HERMES_HTTP_PORT", "9119"
    ):
        raise OperationError("Hermes must publish only the configured loopback port.")
    security = inspected["HostConfig"]
    if (
        security.get("Privileged")
        or security.get("NetworkMode") == "host"
        or security.get("PidMode")
    ):
        raise OperationError(
            "Privileged containers and host/shared process namespaces are unsupported."
        )
    if set(security.get("CapDrop") or []) != {"ALL"} or {
        name.removeprefix("CAP_") for name in security.get("CapAdd") or []
    } != {
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETGID",
        "SETUID",
        "KILL",
    }:
        raise OperationError("Runtime capabilities differ from the reviewed bootstrap allowance.")
    if "no-new-privileges:true" not in security.get("SecurityOpt", []):
        raise OperationError("The no-new-privileges control is missing.")
    if not security.get("Memory") or not security.get("PidsLimit") or not security.get("NanoCpus"):
        raise OperationError("Runtime CPU, memory and process limits are required.")
    logging = security.get("LogConfig", {})
    if logging.get("Type") != "local" or not all(
        logging.get("Config", {}).get(key) for key in ("max-size", "max-file")
    ):
        raise OperationError("Bounded local runtime logs are required.")
    for path in (runtime.settings.env_file, runtime.settings.auth_file):
        metadata = path.stat()
        if (
            path.is_symlink()
            or not path.is_file()
            or metadata.st_uid != 0
            or metadata.st_mode & 0o077
        ):
            raise OperationError("Host configuration must be regular root-only files.")
    if inspected["State"]["Running"]:
        source = (
            "import json; from pathlib import Path; from gateway.status import read_runtime_status; "
            "s=read_runtime_status() or {}; p=int(s.get('pid',0)); "
            "v=Path(f'/proc/{p}/status').read_text(); "
            "print(json.dumps([int(x) for x in next(l for l in v.splitlines() if l.startswith('Uid:')).split()[1:]]))"
        )
        identities = json.loads(
            runtime.compose(
                "exec",
                "-T",
                "--user",
                "hermes",
                "hermes",
                "/opt/hermes/.venv/bin/python",
                "-c",
                source,
            )
        )
        if identities != [int(runtime.settings.values["HERMES_UID"])] * 4:
            raise OperationError("The gateway must execute as the dedicated non-root Hermes user.")
    companion = runtime.companion()
    if companion:
        worker = json.loads(runtime.run(["docker", "inspect", companion]))[0]
        controls = worker["HostConfig"]
        expected_profile = runtime.settings.data_dir / ".council/secretary"
        mounts = worker["Mounts"]
        if (
            len(mounts) != 1
            or Path(mounts[0]["Source"]) != expected_profile
            or mounts[0]["Destination"] != "/opt/data"
            or not mounts[0]["RW"]
        ):
            raise OperationError("The secretary must mount only its own profile.")
        if (
            controls.get("PortBindings")
            or controls.get("Privileged")
            or controls.get("NetworkMode") == "host"
            or controls.get("PidMode")
        ):
            raise OperationError("The secretary must have no published ports or host namespaces.")
        if (
            not controls.get("ReadonlyRootfs")
            or controls.get("CapAdd")
            or set(controls.get("CapDrop") or []) != {"ALL"}
            or "no-new-privileges:true" not in controls.get("SecurityOpt", [])
        ):
            raise OperationError("Secretary isolation controls are missing.")
        if (
            worker["Config"]["User"]
            != runtime.settings.values["HERMES_UID"] + ":" + runtime.settings.values["HERMES_GID"]
        ):
            raise OperationError("The secretary must run as its configured non-root user.")
        for name in ("Memory", "PidsLimit", "NanoCpus"):
            if not controls.get(name):
                raise OperationError("Secretary resource limits are required.")
    if runtime.settings.secretary_file.exists():
        metadata = runtime.settings.secretary_file.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o077:
            raise OperationError("The secretary machine key must remain root-only.")
    secretary = secretary_status(runtime.settings, inspected)
    if secretary["enabled"]:
        secretary["generation"] = generation_status(runtime.settings.data_dir)
    if companion:
        secretary["worker_status"] = worker["State"]["Status"]
        secretary["worker_health"] = worker["State"].get("Health", {}).get("Status")
    elif secretary["enabled"]:
        secretary["worker_status"] = "missing"
    backups = backup_status(runtime.settings)
    backups["timer"] = unit_status(runtime, "agent-council-backup.timer")
    backups["service"] = unit_status(runtime, "agent-council-backup.service")
    instructions = instruction_status(runtime.settings.data_dir)
    rejected = runtime.settings.env_file.with_name("instructions-update-failure.json")
    if rejected.is_file() and not rejected.is_symlink():
        try:
            revision = json.loads(rejected.read_text())["revision"]
            if re.fullmatch(r"[a-f0-9]{40}", revision):
                instructions["rejected_revision"] = revision
        except (OSError, ValueError, TypeError, KeyError):
            instructions["failure_record_available"] = False
    instructions["timer"] = unit_status(runtime, "agent-council-skills.timer")
    instructions["service"] = unit_status(runtime, "agent-council-skills.service")
    research = research_status(runtime.settings)
    research["timer"] = unit_status(runtime, "agent-council-research.timer")
    from .recurring import status as recurring_status

    return {
        "recurring": recurring_status(runtime.settings),
        "status": inspected["State"]["Status"],
        "health": inspected["State"].get("Health", {}).get("Status"),
        "image": inspected["Image"],
        "loopback_only": True,
        "mounts_verified": True,
        "non_root_gateway": inspected["State"]["Running"],
        "secretary": secretary,
        "backups": backups,
        "instructions": instructions,
        "research": research,
        "linear": inspect_services(runtime),
    }
