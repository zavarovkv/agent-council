"""Private interactive reader authorization and native daily report orchestration."""

import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from telegram_monitor.config import Settings as ReaderSettings
from telegram_monitor.config import source_name
from telegram_monitor.delivery import read_json
from telegram_monitor.evidence import ZONE, daily_window
from telegram_secretary.policy import pinned_owner

from .config import OperationError, atomic_write, read_settings
from .instructions import verify_capabilities
from .lifecycle import identity, smoke
from .snapshots import snapshot


def load_reader(path):
    try:
        metadata = path.stat()
        if (
            path.resolve() != path
            or not path.is_file()
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
            or metadata.st_size > 8192
        ):
            raise ValueError("Unsafe credential file.")
        return ReaderSettings.load(read_settings(path))
    except (OSError, ValueError, KeyError, TypeError):
        raise OperationError(
            "Use a private collector credential file created by interactive setup."
        ) from None


def workspace(runtime):
    root = runtime.settings.workspace_dir / "monitoring/telegram"
    marker = root / ".managed"
    if root.resolve() != root or (root.exists() and any(root.iterdir()) and not marker.is_file()):
        raise OperationError("Preserve unmanaged Telegram evidence directories.")
    if marker.exists() and read_json(marker) != {"schema": 1, "managed_by": "agent-council"}:
        raise OperationError("Preserve unrecognized Telegram monitor ownership.")
    uid, gid = identity(runtime.settings)
    for path in (root, root / "raw", root / "reports"):
        if path.resolve() != path:
            raise OperationError("Refusing a symlinked monitor directory.")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(path, uid, gid)
        path.chmod(0o700)
    atomic_write(marker, b'{"schema":1,"managed_by":"agent-council"}\n')
    os.chown(marker, uid, gid)
    return root


def provision_copy(runtime, home, reader, owner):
    uid, gid = identity(runtime.settings)
    runtime.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            f"{uid}:{gid}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "512m",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "-e",
            "HERMES_HOME=/opt/data",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "--mount",
            f"type=bind,source={home},target=/opt/data",
            "--workdir",
            "/opt/agent-council",
            "--entrypoint",
            "/opt/hermes/.venv/bin/python",
            runtime.image()[0],
            "-m",
            "telegram_monitor.schedule",
            reader.source,
            str(reader.peer),
            str(owner),
            "prepare",
        ]
    )


def collector_units(runtime):
    units = []
    for suffix in ("service", "timer"):
        source = runtime.settings.repository / f"config/agent-council-telegram-reader.{suffix}"
        destination = Path("/etc/systemd/system") / source.name
        if destination.is_symlink() or (
            destination.exists() and destination.read_bytes() != source.read_bytes()
        ):
            raise OperationError("Preserve an existing collector unit before replacing it.")
        units.append((source, destination))
    return units


def enable_timer(runtime):
    for source, destination in collector_units(runtime):
        atomic_write(destination, source.read_bytes(), mode=0o644)
    runtime.run(["systemctl", "daemon-reload"])
    runtime.run(["systemctl", "enable", "--now", "agent-council-telegram-reader.timer"])


def collect(runtime):
    settings = runtime.settings
    reader = load_reader(settings.telegram_reader_file)
    root = workspace(runtime)
    started = time.time()
    try:
        runtime.compose("run", "--rm", "--no-deps", "-T", "telegram-reader", timeout=180)
    except OperationError:
        pass  # The fresh artifact, not an exception string, is the collection receipt.
    _, end = daily_window(datetime.now(UTC))
    artifact = root / "raw" / (end.astimezone(ZONE).date().isoformat() + ".json")
    try:
        evidence = read_json(artifact, 2 * 1048576)
        generated = datetime.fromisoformat(evidence["generated_at"])
        if (
            generated.tzinfo is None
            or generated.timestamp() < started - 10
            or evidence.get("source") != reader.source
            or evidence.get("peer_id") != reader.peer
            or evidence.get("window_end") != end.isoformat()
            or evidence.get("status") not in {"complete", "incomplete", "unavailable"}
        ):
            raise ValueError("No fresh source receipt.")
    except (OSError, ValueError, KeyError, TypeError):
        raise OperationError(
            "Collection was not confirmed; no stale artifact was accepted."
        ) from None
    return {
        "status": evidence["status"],
        "coverage": evidence.get("coverage"),
        "reason": evidence.get("reason"),
        "window_end": end.isoformat(),
        "candidate_count": evidence.get("candidate_count"),
        "artifact": str(artifact),
    }


def configure(runtime, source, *, reauthorize=False):
    source = source_name(source)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise OperationError(
            "Use the interactive setup script; never pass credentials as arguments."
        )
    verify_capabilities(runtime, ["telegram-monitor-v1"])
    runtime.require_idle()
    settings = runtime.settings
    owner = pinned_owner(settings.data_dir)
    if owner is None:
        raise OperationError("Complete verified owner sign-in before setting a report destination.")
    if not (settings.data_dir / "skills/travel-digest/SKILL.md").is_file():
        raise OperationError("Install the reviewed travel-digest skill before setup.")
    workspace(runtime)
    collector_units(runtime)
    target = settings.telegram_reader_file
    if target.is_symlink():
        raise OperationError("Refusing a symlinked reader session.")
    previous = load_reader(target) if target.exists() else None
    marker = settings.data_dir / ".council/telegram-monitor.json"
    configuration = read_json(marker) if marker.exists() else None
    if configuration and (
        configuration.get("source") != source
        or configuration.get("owner_id") != owner
        or type(configuration.get("peer_id")) is not int
        or configuration["peer_id"] <= 0
    ):
        raise OperationError("Preserve the existing monitor source and owner pins.")
    expected_peer = (
        previous.peer if previous else configuration["peer_id"] if configuration else None
    )
    if previous and previous.source != source:
        raise OperationError(
            "Preserve the existing source pin; reconcile a source change explicitly."
        )
    if previous and configuration and previous.peer != configuration["peer_id"]:
        raise OperationError("The host and runtime source pins disagree; preserve them for review.")
    if previous is None or reauthorize:
        uid, gid = identity(settings)
        with tempfile.TemporaryDirectory(
            prefix=".telegram-reader-", dir=settings.env_file.parent
        ) as temporary:
            directory = Path(temporary)
            os.chown(directory, uid, gid)
            runtime.compose(
                "run",
                "--rm",
                "--no-deps",
                "--interactive",
                "--entrypoint",
                "/opt/telegram-reader/bin/python",
                "--volume",
                f"{directory}:/setup",
                "--env",
                f"COUNCIL_TG_READER_SOURCE={source}",
                "--env",
                f"COUNCIL_TG_READER_EXPECTED_PEER={expected_peer or ''}",
                "telegram-reader",
                "-m",
                "telegram_monitor.setup",
                interactive=True,
                timeout=900,
            )
            result = directory / "telegram-reader.env"
            if (
                result.resolve() != result
                or not result.is_file()
                or result.stat().st_mode & 0o077
                or result.stat().st_size > 8192
            ):
                raise OperationError("Interactive authorization did not produce a private session.")
            reader = ReaderSettings.load(read_settings(result))
            if reader.source != source or (
                expected_peer is not None and reader.peer != expected_peer
            ):
                raise OperationError("The source username no longer refers to the pinned chat.")
            # Retain the validated login if later setup is interrupted; never request it twice.
            atomic_write(target, result.read_bytes())
    reader = load_reader(target)
    settings.update_values({"COUNCIL_TG_READER_ENV_FILE": str(target)})
    backup = snapshot(runtime, automatic=True)
    smoke(runtime, backup, prepare_copy=lambda home: provision_copy(runtime, home, reader, owner))
    result = collect(runtime)
    if result["status"] == "unavailable":
        raise OperationError(
            "The session is saved, but collection is unavailable. Rerun setup to retry without logging in again."
        )
    command = (
        "exec",
        "-T",
        "--user",
        "hermes",
        "--workdir",
        "/opt/agent-council",
        "hermes",
        "/opt/hermes/.venv/bin/python",
        "-m",
        "telegram_monitor.schedule",
        reader.source,
        str(reader.peer),
        str(owner),
    )
    runtime.compose(*command, "prepare")
    enable_timer(runtime)
    schedule = json.loads(runtime.compose(*command, "activate"))
    runtime.check_protected()
    return {
        **schedule,
        "source": source,
        "collection": result,
        "backup": str(backup),
        "credentials_exposed_to_agents": False,
    }
