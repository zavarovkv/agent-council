"""Private consistent restore points; restic handles encrypted off-host storage."""

import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from .config import OperationError, atomic_write
from .linear_state import maintenance

# Only disposable directories at a Hermes profile root; workspace content is never filtered.
DISPOSABLE = frozenset(
    {
        "cache",
        "tools",
        "installs",
        "logs",
        "telemetry",
        "backups",
        "migration-backup",
        ".curator_backups",
        ".operator",
        "audio_cache",
        "image_cache",
        "plugin-update-checks",
        "source-checks",
        "runtime",
        "gateway_state.json",
        "gateway.pid",
        "gateway.lock",
    }
)


def disposable_path(relative):
    parts = relative.parts
    return bool(parts) and (
        parts[0] in DISPOSABLE
        or (len(parts) >= 3 and parts[0] == "profiles" and parts[2] in DISPOSABLE)
        or (len(parts) >= 3 and parts[:2] == (".council", "secretary") and parts[2] in DISPOSABLE)
        or (
            len(parts) >= 4
            and parts[:2] == (".council", "linear")
            and parts[2] in {"bridge", "qui-gon", "dexter", "marketing"}
            and parts[3] in DISPOSABLE
        )
    )


def checksum(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def tree_fingerprint(root):
    result = {}
    if root.is_symlink() or not root.is_dir():
        raise OperationError("A regular snapshot directory is required.")
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(parent) / name
            relative = str(path.relative_to(root))
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                result[relative] = {"link": os.readlink(path)}
            elif stat.S_ISREG(metadata.st_mode):
                result[relative] = {
                    "sha256": checksum(path),
                    "mode": stat.S_IMODE(metadata.st_mode),
                }
            elif stat.S_ISDIR(metadata.st_mode):
                result[relative] = {"directory": True, "mode": stat.S_IMODE(metadata.st_mode)}
            else:
                raise OperationError("A snapshot contains an unsupported special file.")
    return result


def copy_state(source, target, *, uid=None, gid=None, portable=False, deadline=None):
    if source.is_symlink() or not source.is_dir() or target.exists() or target.is_symlink():
        raise OperationError("Copy state only between a regular source and a new destination.")

    def ignore_special(parent, names):
        ignored = []
        for name in names:
            item = Path(parent) / name
            if portable and disposable_path(item.relative_to(source)):
                ignored.append(name)
                continue
            mode = item.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                ignored.append(name)  # Gateway sockets are ephemeral, not persistent state.
        return ignored

    def copy_file(source_file, target_file):
        if deadline is not None and time.monotonic() >= deadline:
            raise OperationError("Snapshot freeze budget exceeded; checkpoint discarded.")
        return shutil.copy2(source_file, target_file)

    shutil.copytree(source, target, symlinks=True, ignore=ignore_special, copy_function=copy_file)
    target.chmod(0o700)
    if uid is not None:
        for parent, directories, files in os.walk(target, followlinks=False):
            os.chown(parent, uid, gid, follow_symlinks=False)
            for name in directories + files:
                os.chown(Path(parent) / name, uid, gid, follow_symlinks=False)


def load_manifest(backup):
    backup = Path(backup).absolute()
    if backup.resolve() != backup or not backup.is_dir():
        raise OperationError("Use a regular restore-point directory without symlinks.")
    path = backup / "manifest.json"
    if path.is_symlink():
        raise OperationError("Refusing a symlinked snapshot manifest.")
    manifest = json.loads(path.read_text())
    if manifest.get("application") != "agent-council" or manifest.get("schema") != 1:
        raise OperationError("Unsupported or incomplete restore point.")
    expected = {"compose.env", "auth.env", "compose.yaml"}
    if not expected.issubset(manifest.get("files", {})):
        raise OperationError("The restore point is missing required files.")
    for name, digest in manifest["files"].items():
        if name not in expected | {"image.tar", "secretary.env"}:
            raise OperationError("Unexpected snapshot manifest file.")
        item = backup / name
        if item.is_symlink() or not item.is_file() or checksum(item) != digest:
            raise OperationError("Snapshot checksum verification failed.")
    for name in ("hermes", "workspace"):
        if tree_fingerprint(backup / name) != manifest.get("state", {}).get(name):
            raise OperationError("Snapshot state verification failed.")
    return backup, manifest


@maintenance
def snapshot(runtime, *, include_image=False, restart=True, portable=False):
    settings = runtime.settings
    settings.backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings.backup_dir.chmod(0o700)
    image, labels = runtime.image()
    running_services = runtime.running_services()
    running = runtime.inspect()["State"]["Running"]
    if not running and any(service != "hermes" for service in running_services):
        raise OperationError(
            "Stop all owned companion services before copying a stopped deployment."
        )
    runtime.require_idle()
    size = 0
    for root in (settings.data_dir, settings.workspace_dir):
        for parent, directories, files in os.walk(root, followlinks=False):
            if portable and root == settings.data_dir:
                directories[:] = [
                    name
                    for name in directories
                    if not disposable_path((Path(parent) / name).relative_to(root))
                ]
                files = [
                    name
                    for name in files
                    if not disposable_path((Path(parent) / name).relative_to(root))
                ]
            size += sum(
                (Path(parent) / name).lstat().st_size
                for name in files
                if stat.S_ISREG((Path(parent) / name).lstat().st_mode)
            )
    image_size = 0
    if include_image:
        image_size = json.loads(runtime.run(["docker", "image", "inspect", image]))[0]["Size"]
    if shutil.disk_usage(settings.backup_dir).free < size * 2 + image_size * 2 + 512 * 1024**2:
        raise OperationError("Insufficient free space for a snapshot and recovery verification.")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(tempfile.mkdtemp(prefix=f"backup-{stamp}-", dir=settings.backup_dir))
    files = {}
    if include_image:
        runtime.run(
            ["docker", "image", "save", "--output", str(backup / "image.tar"), image], timeout=900
        )
        (backup / "image.tar").chmod(0o600)
        files["image.tar"] = checksum(backup / "image.tar")
    complete = False
    stopped = False
    try:
        if running and not restart:
            stopped = True
            runtime.stop()
        # Routine snapshots freeze all writers rather than recreating gateways.
        # Code cutovers retain the stopped, consistent rollback point as before.
        with runtime.frozen() if running and restart else nullcontext(None) as deadline:
            for source, target in (
                (settings.env_file, "compose.env"),
                (settings.auth_file, "auth.env"),
                (runtime.compose_source(), "compose.yaml"),
            ):
                if source.is_symlink() or not source.is_file():
                    raise OperationError("Refusing a missing or symlinked host configuration.")
                atomic_write(backup / target, source.read_bytes())
            if settings.secretary_file.exists():
                atomic_write(backup / "secretary.env", settings.secretary_file.read_bytes())
                files["secretary.env"] = checksum(backup / "secretary.env")
            copy_state(settings.data_dir, backup / "hermes", portable=portable, deadline=deadline)
            copy_state(settings.workspace_dir, backup / "workspace", deadline=deadline)
        if running and restart:
            runtime.check_protected()
        for name in ("compose.env", "auth.env", "compose.yaml"):
            files[name] = checksum(backup / name)
        manifest = {
            "application": "agent-council",
            "schema": 1,
            "kind": "portable-data" if portable else "full-local-checkpoint",
            "excluded_profile_entries": sorted(DISPOSABLE) if portable else [],
            "created_at": datetime.now(UTC).isoformat(),
            "image_id": image,
            "running_services": running_services,
            "revision": labels.get("org.opencontainers.image.revision")
            or (
                settings.env_file.with_name("revision").read_text().strip()
                if settings.env_file.with_name("revision").is_file()
                else None
            ),
            "files": files,
            "state": {name: tree_fingerprint(backup / name) for name in ("hermes", "workspace")},
        }
        atomic_write(backup / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
        complete = True
    finally:
        if stopped and not complete:
            runtime.start()
            runtime.ready()
            runtime.check_protected()
    return backup
