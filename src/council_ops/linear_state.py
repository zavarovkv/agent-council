"""Optional Linear lifecycle boundaries, including admission pause during cutover."""

import functools
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from .config import OperationError, atomic_write

SERVICES = ("linear-adapter", "linear-executor", "dexter-executor")


def root(settings):
    return settings.data_dir / ".council/linear"


def require_capability(runtime, *homes, image=None):
    from .instructions import verify_capabilities

    if any((home / ".council/linear/.managed").exists() for home in homes):
        verify_capabilities(runtime, ["linear-agents-v1"], image=image)


@contextmanager
def quiesce(runtime):
    directory = root(runtime.settings) / "bridge"
    if not directory.exists():
        yield
        return
    if directory.resolve() != directory:
        raise OperationError("Refusing a symlinked integration profile.")
    count = getattr(runtime, "_linear_maintenance_depth", 0)
    runtime._linear_maintenance_depth = count + 1
    marker = directory / "runtime/maintenance"
    if count == 0:
        marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(
            marker.parent,
            int(runtime.settings.values["HERMES_UID"]),
            int(runtime.settings.values["HERMES_GID"]),
        )
        atomic_write(marker, b"maintenance\n")
    try:
        yield
    finally:
        runtime._linear_maintenance_depth -= 1
        if runtime._linear_maintenance_depth == 0:
            marker.unlink(missing_ok=True)


def maintenance(function):
    @functools.wraps(function)
    def wrapped(runtime, *args, **kwargs):
        with quiesce(runtime):
            return function(runtime, *args, **kwargs)

    return wrapped


def status(settings):
    from linear_agent.config import SLUGS, Registry

    directory = root(settings) / "bridge"
    if not directory.exists():
        return {"configured": False}
    if not (directory / "agents.json").exists():
        return {"prepared": True, "configured": False, "enabled": False}
    try:
        registry = Registry.load(directory)
        # Read-only diagnostics must not create/migrate the live database.
        path = directory / "linear.sqlite3"
        if path.resolve() != path or not path.is_file():
            raise ValueError("No integration store")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Invalid integration store")
            jobs = dict(db.execute("SELECT state,count(*) FROM jobs GROUP BY state"))
            outbox = dict(db.execute("SELECT state,count(*) FROM outbox GROUP BY state"))
            installed = db.execute(
                "SELECT count(*) FROM installations WHERE organization=?", (registry.organization,)
            ).fetchone()[0]
        return {
            "configured": True,
            "available": True,
            "enabled": registry.enabled,
            "registered_agents": len(registry.agents),
            "required_services": ["linear-adapter", *(SLUGS[slug] for slug in registry.agents)],
            "installed_agents": installed,
            "jobs": jobs,
            "outbox": outbox,
            "admission_paused": (directory / "runtime/maintenance").exists(),
        }
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return {"configured": True, "available": False, "enabled": False}


def inspect_services(runtime):
    """Inspect only owned containers; never expose Config.Env or credentials."""
    report = status(runtime.settings)
    report["services"] = {
        service: {"status": "missing", "health": None}
        for service in report.get("required_services", [])
    }
    if not root(runtime.settings).exists():
        return report
    expected_user = (
        runtime.settings.values["HERMES_UID"] + ":" + runtime.settings.values["HERMES_GID"]
    )
    for service, container, _running in runtime.linear_companions():
        inspected = json.loads(runtime.run(["docker", "inspect", container]))[0]
        controls = inspected["HostConfig"]
        profile = (
            "bridge"
            if service == "linear-adapter"
            else "qui-gon"
            if service == "linear-executor"
            else "dexter"
        )
        home = root(runtime.settings) / profile
        expected = {"/opt/data": home}
        if profile != "bridge":
            expected["/workspace"] = home / "workspace"
        mounts = inspected["Mounts"]
        if len(mounts) != len(expected) or any(
            expected.get(mount["Destination"]) != Path(mount["Source"]) or not mount["RW"]
            for mount in mounts
        ):
            raise OperationError("A Linear service must mount only its own profile and workspace.")
        if (
            inspected["Config"]["User"] != expected_user
            or controls.get("Init") is not True
            or not controls.get("ReadonlyRootfs")
            or controls.get("CapAdd")
            or set(controls.get("CapDrop") or []) != {"ALL"}
            or "no-new-privileges:true" not in controls.get("SecurityOpt", [])
            or controls.get("Privileged")
            or controls.get("PidMode")
            or controls.get("NetworkMode") == "host"
        ):
            raise OperationError("Linear service isolation differs from the reviewed policy.")
        bindings = controls.get("PortBindings") or {}
        expected_port = runtime.settings.values.get("COUNCIL_LINEAR_HTTP_PORT", "9121")
        if bindings != (
            {"9121/tcp": [{"HostIp": "127.0.0.1", "HostPort": expected_port}]}
            if profile == "bridge"
            else {}
        ):
            raise OperationError(
                "Publish only the adapter's loopback port; executor APIs must stay private."
            )
        if (
            not all(controls.get(name) for name in ("Memory", "PidsLimit", "NanoCpus"))
            or controls.get("LogConfig", {}).get("Type") != "local"
        ):
            raise OperationError("Linear services require bounded resources and local logs.")
        networks = inspected.get("NetworkSettings", {}).get("Networks", {})
        if set(networks) != {"agent-council_linear"}:
            raise OperationError("Linear services require their separate execution network.")
        report["services"][service] = {
            "status": inspected["State"]["Status"],
            "health": inspected["State"].get("Health", {}).get("Status"),
        }
    return report
