"""Small operator CLI; secrets and mutable state never enter the Git checkout."""

import argparse
import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path

from .config import OperationError, Settings
from .doctor import doctor
from .guard import ServiceGuard
from .install import install, recover
from .lifecycle import deploy, restore, smoke
from .restic import Restic, cloud_backup
from .runtime import Runtime
from .secretary import prepare as prepare_secretary
from .setup import configure_backups
from .snapshots import snapshot


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    result.add_argument(
        "--env-file",
        type=Path,
        default=os.environ.get("COUNCIL_COMPOSE_ENV_FILE", "/etc/agent-council/compose.env"),
    )
    result.add_argument(
        "--backup-dir",
        type=Path,
        default=os.environ.get("COUNCIL_BACKUP_DIR", "/var/backups/agent-council"),
    )
    commands = result.add_subparsers(dest="action", required=True)
    secretary = commands.add_parser(
        "prepare-secretary", help="Prepare a separate tool-free Telegram secretary profile"
    )
    secretary.add_argument(
        "--owner-name",
        required=True,
        help="Explicit account-owner display name; no implicit repository owner",
    )
    secretary.add_argument("--assistant-name", default="Ассистент")
    commands.add_parser("doctor", help="Read safe configuration, isolation and service status")
    commands.add_parser(
        "install", help="Initialize a new loopback-only deployment with no default owner"
    )
    adopt = commands.add_parser(
        "adopt-release",
        help="Record the source of an existing deployed release without restarting it",
    )
    adopt.add_argument("--revision", required=True)
    backup = commands.add_parser("backup", help="Create a consistent local restore point")
    backup.add_argument(
        "--include-image", action="store_true", help="Include the exact image for offline recovery"
    )
    backup.add_argument(
        "--portable",
        action="store_true",
        help="Copy persistent data only, using the cloud exclusion policy",
    )
    verify = commands.add_parser(
        "verify-backup", help="Restore a copy and verify it in an offline container"
    )
    verify.add_argument("backup", type=Path)
    commands.add_parser(
        "configure-backups", help="Enter R2 credentials and recovery password in a private terminal"
    )
    commands.add_parser(
        "deploy", help="Build the clean checkout, test a copied profile and switch Hermes"
    )
    recovery = commands.add_parser(
        "restore", help="Restore this deployment, retaining displaced directories"
    )
    recovery.add_argument("backup", type=Path)
    recovery.add_argument(
        "--confirm", action="store_true", help="Explicitly authorize replacing live Hermes state"
    )
    fresh = commands.add_parser(
        "recover", help="Recover onto a prepared empty replacement host using the saved image"
    )
    fresh.add_argument("backup", type=Path)
    fresh.add_argument("--confirm", action="store_true")
    fresh.add_argument(
        "--rebuild",
        action="store_true",
        help="Build the backup's exact Git revision instead of requiring its local image",
    )
    commands.add_parser(
        "init-cloud-backups", help="Initialize the configured private encrypted R2 repository"
    )
    commands.add_parser(
        "cloud-backup", help="Back up to R2 and prove recovery before applying retention"
    )
    check = commands.add_parser("check-cloud-backups", help="Check encrypted repository integrity")
    check.add_argument("--read-data", action="store_true", help="Verify all stored encrypted data")
    download = commands.add_parser(
        "download-backup", help="Recover R2 data to a private staging directory only"
    )
    download.add_argument("snapshot_id")
    return result


def main(argv=None):
    arguments = parser().parse_args(argv)
    os.umask(0o077)
    settings = Settings.load(arguments.repository, arguments.env_file, arguments.backup_dir)
    if os.geteuid() != 0:
        raise OperationError("Run deployment operations as root on the Docker host.")
    lock_path = settings.env_file.with_name("operations.lock")
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    with os.fdopen(os.open(lock_path, flags, 0o600), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime = Runtime(settings)
        runtime.preflight()
        runtime.guard = ServiceGuard(settings.env_file.with_name("protected-services.json"))
        if arguments.action in ("backup", "deploy", "restore", "cloud-backup"):
            doctor(runtime)
        if arguments.action == "doctor":
            result = doctor(runtime)
        elif arguments.action == "prepare-secretary":
            doctor(runtime)
            result = prepare_secretary(
                runtime, owner_name=arguments.owner_name, assistant_name=arguments.assistant_name
            )
        elif arguments.action == "install":
            result = install(runtime)
        elif arguments.action == "recover":
            if not arguments.confirm:
                raise OperationError("Replacement-host recovery requires --confirm.")
            result = recover(runtime, arguments.backup, rebuild=arguments.rebuild)
        elif arguments.action == "adopt-release":
            runtime.adopt_release(arguments.revision)
            result = {"adopted_revision": arguments.revision, "service_restarted": False}
        elif arguments.action == "backup":
            result = {
                "backup": str(
                    snapshot(
                        runtime, include_image=arguments.include_image, portable=arguments.portable
                    )
                )
            }
        elif arguments.action == "verify-backup":
            manifest = smoke(runtime, arguments.backup)
            result = {"verified": True, "image": manifest["image_id"], "external_network": False}
        elif arguments.action == "deploy":
            result = deploy(runtime)
        elif arguments.action == "restore":
            if not arguments.confirm:
                raise OperationError(
                    "Restore requires --confirm; current directories will be retained."
                )
            result = {"retained": restore(runtime, arguments.backup)}
        elif arguments.action == "init-cloud-backups":
            Restic(runtime).initialize()
            result = {"encrypted_repository_initialized": True}
        elif arguments.action == "configure-backups":
            result = configure_backups(runtime)
        elif arguments.action == "cloud-backup":
            result = cloud_backup(runtime)
        elif arguments.action == "check-cloud-backups":
            Restic(runtime).check(full=arguments.read_data)
            result = {"cloud_integrity_verified": True, "read_all_data": arguments.read_data}
        elif arguments.action == "download-backup":
            settings.backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination = Path(tempfile.mkdtemp(prefix="cloud-recovery-", dir=settings.backup_dir))
            restored = Restic(runtime).download(arguments.snapshot_id, destination)
            result = {
                "downloaded": str(restored),
                "checksums_verified": True,
                "live_state_changed": False,
            }
        runtime.check_protected()
        print(json.dumps(result, ensure_ascii=False))


def entrypoint():
    try:
        main()
    except (OperationError, OSError, ValueError, KeyError) as error:
        if isinstance(error, OperationError):
            print(str(error), file=sys.stderr)
        else:
            print(
                f"Operation stopped ({type(error).__name__}); no credentials were printed.",
                file=sys.stderr,
            )
        sys.exit(1)
