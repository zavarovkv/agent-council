"""Small operator CLI; secrets and mutable state never enter the Git checkout."""

import argparse
import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path

from . import linear
from .config import OperationDeferred, OperationError, Settings
from .doctor import doctor, unit_status
from .guard import ServiceGuard
from .guests import configure as configure_guests
from .install import install, recover
from .instructions import apply as apply_instructions
from .instructions import export as export_instructions
from .instructions import sync as sync_instructions
from .lifecycle import deploy, restore, smoke
from .monitoring import configure as configure_monitoring
from .monitoring import poll as monitor
from .recurring import configure as configure_recurring
from .research import configure as configure_research
from .research import flush as flush_research
from .restic import Restic, cloud_backup
from .routing import configure as configure_route
from .runtime import Runtime
from .secretary import prepare as prepare_secretary
from .setup import configure_backups
from .snapshots import snapshot
from .telegram_reader import collect as collect_telegram
from .telegram_reader import configure as configure_telegram
from .workspace import configure as configure_workspace
from .workspace_index import configure as configure_workspace_index


def pause_instruction_updates(runtime):
    state = unit_status(runtime, "agent-council-skills.timer")
    if state.get("ActiveState") == "active" or state.get("UnitFileState") in {
        "enabled",
        "enabled-runtime",
    }:
        runtime.run(["systemctl", "disable", "--now", "agent-council-skills.timer"])


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
    telegram = commands.add_parser(
        "configure-telegram-monitor",
        help="Privately authorize one pinned Telegram source",
    )
    telegram.add_argument("source", help="Existing source username; no @ or URL")
    telegram.add_argument("--reauthorize", action="store_true")
    commands.add_parser(
        "collect-telegram-monitor", help="Collect the pinned source without sending messages"
    )
    instructions = commands.add_parser(
        "apply-instructions", help="Install a reviewed private instruction checkout"
    )
    instructions.add_argument("source", type=Path)
    instructions.add_argument("--revision", required=True, help="Full reviewed commit SHA")
    instructions.add_argument(
        "--adopt",
        action="append",
        default=[],
        metavar="PATH=SHA256",
        help="Adopt one reviewed unmanaged instruction at its exact current checksum",
    )
    instructions.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch and select this exact revision under the maintenance lock",
    )
    export = commands.add_parser(
        "export-instructions", help="Stage runtime skill edits in a private checkout for review"
    )
    export.add_argument("source", type=Path)
    export.add_argument("--revision", required=True)
    export.add_argument(
        "--include-skill",
        action="append",
        default=[],
        help="Also export this relative main-profile skill directory",
    )
    sync = commands.add_parser(
        "sync-instructions", help="Apply the private main branch through the guarded installer"
    )
    sync.add_argument("source", type=Path)
    secretary = commands.add_parser(
        "prepare-secretary", help="Prepare a separate tool-free Telegram secretary profile"
    )
    secretary.add_argument(
        "--owner-name",
        required=True,
        help="Explicit account-owner display name; no implicit repository owner",
    )
    secretary.add_argument("--assistant-name", default="Ассистент")
    guests = commands.add_parser(
        "guest-mode", help="Read or configure owner-only Telegram guest replies"
    )
    choices = guests.add_mutually_exclusive_group()
    choices.add_argument("--enable", action="store_true")
    choices.add_argument("--disable", action="store_true")
    guests.add_argument(
        "--owner-agent",
        action="store_true",
        default=None,
        help="Use the main assistant for the pinned owner's guest calls",
    )
    commands.add_parser(
        "configure-workspace", help="Share Notion and Drive tools with all trusted agents"
    )
    commands.add_parser("doctor", help="Read safe configuration, isolation and service status")
    prepare_linear = commands.add_parser(
        "prepare-linear-agents", help="Prepare isolated Linear executors without model credentials"
    )
    prepare_linear.add_argument("--owner", required=True, help="Immutable Linear human user ID")
    prepare_linear.add_argument("--organization", required=True, help="Immutable workspace ID")
    prepare_linear.add_argument(
        "--team",
        action="append",
        required=True,
        help="Explicit team ID; repeat for additional teams",
    )
    prepare_linear.add_argument("--with-dexter", action="store_true")
    prepare_linear.add_argument("--with-marketing", action="store_true")
    prepare_linear.add_argument("--with-padme", action="store_true")
    configure_linear = commands.add_parser(
        "configure-linear-agents",
        help="Enter app credentials privately; execution remains disabled",
    )
    configure_linear.add_argument(
        "--agent",
        choices=["dexter", "marketing", "padme"],
        help="Add only this new prepared agent; preserve existing grants and runs",
    )
    commands.add_parser(
        "connect-linear-agents", help="Open a 15-minute browser-bound OAuth installation window"
    )
    execution = commands.add_parser(
        "linear-execution", help="Atomically enable or stop Linear execution"
    )
    execution_choice = execution.add_mutually_exclusive_group(required=True)
    execution_choice.add_argument("--enable", action="store_true")
    execution_choice.add_argument("--disable", action="store_true")
    commands.add_parser(
        "configure-research", help="Prepare coordinator-only Kanban and Notion filing"
    )
    commands.add_parser("flush-research", help="Resume queued research filing without model calls")
    commands.add_parser(
        "configure-workspace-index", help="Prepare daily Notion and Drive navigation"
    )
    commands.add_parser("configure-recurring", help="Prepare the owner-pinned scheduling grant")
    commands.add_parser(
        "configure-monitoring", help="Pin operational notifications to the verified owner"
    )
    monitoring = commands.add_parser(
        "monitor", help="Check services and send deduplicated owner alerts"
    )
    monitoring.add_argument("--test-notification", action="store_true")
    commands.add_parser(
        "configure-route", help="Validate and gracefully apply Council's Nginx route"
    )
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
    deployment = commands.add_parser(
        "deploy", help="Build the clean checkout, test a copied profile and switch Hermes"
    )
    deployment.add_argument("--instructions-source", type=Path)
    deployment.add_argument(
        "--instructions-revision", help="Full reviewed private SHA; checkout must already match"
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
    try:
        operate(arguments, settings)
    except OperationDeferred:
        if arguments.action not in {"sync-instructions", "flush-research"}:
            raise
        print(json.dumps({"operation_deferred": arguments.action, "retry": "next_timer_tick"}))


def operate(arguments, settings):
    if arguments.action == "monitor":
        print(json.dumps(monitor(settings, test=arguments.test_notification)))
        return
    lock_path = settings.env_file.with_name("operations.lock")
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    with os.fdopen(os.open(lock_path, flags, 0o600), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OperationDeferred("Another owned operation is active; retry later.") from None
        runtime = Runtime(settings)
        runtime.preflight()
        runtime.guard = ServiceGuard(settings.env_file.with_name("protected-services.json"))
        if arguments.action in (
            "backup",
            "deploy",
            "restore",
            "cloud-backup",
            "apply-instructions",
            "sync-instructions",
        ):
            doctor(runtime)
        if arguments.action == "doctor":
            result = doctor(runtime)
        elif arguments.action == "configure-telegram-monitor":
            result = configure_telegram(
                runtime, arguments.source, reauthorize=arguments.reauthorize
            )
        elif arguments.action == "collect-telegram-monitor":
            result = collect_telegram(runtime)
        elif arguments.action == "guest-mode":
            if arguments.enable:
                doctor(runtime)
            result = configure_guests(
                runtime,
                enabled=True if arguments.enable else False if arguments.disable else None,
                owner_agent=arguments.owner_agent,
            )
        elif arguments.action == "prepare-linear-agents":
            doctor(runtime)
            result = linear.prepare(
                runtime,
                owner=arguments.owner,
                organization=arguments.organization,
                teams=arguments.team,
                with_dexter=arguments.with_dexter,
                with_marketing=arguments.with_marketing,
                with_padme=arguments.with_padme,
            )
        elif arguments.action == "configure-linear-agents":
            result = linear.configure(runtime, selected=arguments.agent)
        elif arguments.action == "connect-linear-agents":
            result = linear.setup_window(runtime)
        elif arguments.action == "linear-execution":
            result = linear.execution(runtime, enabled=arguments.enable)
        elif arguments.action == "configure-route":
            result = configure_route(runtime)
        elif arguments.action == "configure-monitoring":
            result = configure_monitoring(runtime)
        elif arguments.action == "configure-workspace":
            doctor(runtime)
            result = configure_workspace(runtime)
        elif arguments.action == "configure-workspace-index":
            doctor(runtime)
            result = configure_workspace_index(runtime)
        elif arguments.action == "configure-recurring":
            doctor(runtime)
            result = configure_recurring(runtime)
        elif arguments.action == "configure-research":
            result = configure_research(runtime)
        elif arguments.action == "flush-research":
            result = flush_research(runtime)
        elif arguments.action == "apply-instructions":
            adoptions = {}
            for item in arguments.adopt:
                name, separator, checksum = item.partition("=")
                if not separator or name in adoptions:
                    raise OperationError("Use distinct PATH=SHA256 instruction adoptions.")
                adoptions[name] = checksum
            result = apply_instructions(
                runtime,
                arguments.source,
                arguments.revision,
                fetch=arguments.fetch,
                adoptions=adoptions,
            )
        elif arguments.action == "export-instructions":
            result = export_instructions(
                runtime,
                arguments.source,
                arguments.revision,
                include_skills=arguments.include_skill,
            )
        elif arguments.action == "sync-instructions":
            result = sync_instructions(runtime, arguments.source)
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
            result = deploy(
                runtime,
                instruction_source=arguments.instructions_source,
                instruction_revision=arguments.instructions_revision,
            )
        elif arguments.action == "restore":
            if not arguments.confirm:
                raise OperationError(
                    "Restore requires --confirm; current directories will be retained."
                )
            pause_instruction_updates(runtime)
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
