"""Build before downtime, test restored copies and roll back data with the image."""

import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from .config import OperationError, atomic_write, read_settings
from .snapshots import copy_state, load_manifest, snapshot


def identity(settings):
    return int(settings.values["HERMES_UID"]), int(settings.values["HERMES_GID"])


def smoke(runtime, backup, *, image=None, prepare_copy=None, instruction_mode=None):
    backup, manifest = load_manifest(backup)
    image = image or manifest["image_id"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest["image_id"]):
        raise OperationError("The backup does not identify an immutable Docker image.")
    try:
        runtime.run(["docker", "image", "inspect", image])
    except OperationError:
        if image != manifest["image_id"] or "image.tar" not in manifest["files"]:
            raise OperationError(
                "Load the recorded image, or use recover --rebuild on an empty host at the backup's Git revision."
            ) from None
        runtime.run(["docker", "image", "load", "--input", str(backup / "image.tar")], timeout=900)
        runtime.run(["docker", "image", "inspect", image])
    settings = runtime.settings
    mode = instruction_mode or (
        read_settings(backup / "secretary.env").get("COUNCIL_SECRETARY_INSTRUCTIONS", "image")
        if "secretary.env" in manifest["files"]
        else "image"
    )
    if mode not in {"image", "profile"}:
        raise OperationError("Invalid restored secretary instruction source.")
    uid, gid = identity(settings)
    with tempfile.TemporaryDirectory(
        prefix=".restore-check-", dir=settings.backup_dir
    ) as temporary:
        root = Path(temporary)
        roots = {"hermes": root / "hermes", "workspace": root / "workspace"}
        for name, target in roots.items():
            copy_state(backup / name, target, uid=uid, gid=gid)
        if prepare_copy is not None:
            prepare_copy(roots["hermes"])
        from .instructions import installed_requirements, verify_capabilities

        verify_capabilities(runtime, installed_requirements(roots["hermes"]), image=image)
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
                "--cpus",
                "1",
                "--pids-limit",
                "64",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=64m",
                "--env-file",
                str(backup / "auth.env"),
                "-e",
                "HERMES_HOME=/opt/data",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "-e",
                "HERMES_PUBLIC_URL=" + settings.values["HERMES_PUBLIC_URL"],
                "-e",
                "COUNCIL_VERIFY_SECRETARY_INSTRUCTIONS=" + mode,
                "--mount",
                f"type=bind,source={roots['hermes']},target=/opt/data",
                "--mount",
                f"type=bind,source={roots['workspace']},target=/workspace",
                "--mount",
                f"type=bind,source={settings.repository / 'scripts/verify-profile.py'},target=/tmp/verify-profile.py,readonly",
                "--entrypoint",
                "/opt/hermes/.venv/bin/python",
                image,
                "/tmp/verify-profile.py",
            ]
        )
    return manifest


def restore(runtime, backup, *, require_idle=True):
    """Restore only this deployment; retain displaced live directories for recovery."""
    backup, manifest = load_manifest(backup)
    image = manifest["image_id"]
    try:
        runtime.run(["docker", "image", "inspect", image])
    except OperationError:
        if "image.tar" not in manifest["files"]:
            raise OperationError(
                "Load the recorded image or use a backup containing image.tar."
            ) from None
        runtime.run(["docker", "image", "load", "--input", str(backup / "image.tar")], timeout=900)
    smoke(runtime, backup)
    settings = runtime.settings
    uid, gid = identity(settings)
    roots = {"hermes": settings.data_dir, "workspace": settings.workspace_dir}
    stages = {}
    temporary_roots = []
    displaced = {}
    old_env = settings.env_file.read_bytes()
    old_auth = settings.auth_file.read_bytes()
    old_compose = runtime.compose_source().read_bytes()
    old_secretary = (
        settings.secretary_file.read_bytes() if settings.secretary_file.exists() else None
    )
    stopped = False
    try:
        for name, target in roots.items():
            temporary = Path(tempfile.mkdtemp(prefix=".council-restore-", dir=target.parent))
            temporary_roots.append(temporary)
            stages[name] = temporary / name
        for name, target in stages.items():
            copy_state(backup / name, target, uid=uid, gid=gid)
        if require_idle and runtime.inspect()["State"].get("Health", {}).get("Status") == "healthy":
            runtime.require_idle()
        runtime.stop()
        stopped = True
        for name, target in roots.items():
            retained = target.with_name(target.name + ".before-restore-" + uuid.uuid4().hex[:12])
            os.replace(target, retained)
            displaced[name] = retained
            os.replace(stages[name], target)
        # Preserve this host's paths, UID/GID and origin; only the image and auth are restored.
        settings.update_values(
            {
                "COUNCIL_HERMES_IMAGE": image,
                "COUNCIL_SECRETARY_ENABLED": "true"
                if "secretary" in manifest.get("running_services", [])
                else "false",
            }
        )
        runtime.activate_source(backup / "compose.yaml")
        atomic_write(settings.auth_file, (backup / "auth.env").read_bytes())
        if "secretary.env" in manifest["files"]:
            atomic_write(settings.secretary_file, (backup / "secretary.env").read_bytes())
        else:
            settings.secretary_file.unlink(missing_ok=True)
        runtime.start()
        runtime.ready()
        runtime.check_protected()
        if manifest.get("revision"):
            atomic_write(
                settings.env_file.with_name("revision"), (manifest["revision"] + "\n").encode()
            )
    except Exception:
        if stopped:
            runtime.stop()
            for name, retained in reversed(list(displaced.items())):
                target = roots[name]
                if target.exists():
                    failed = target.with_name(
                        target.name + ".failed-restore-" + uuid.uuid4().hex[:12]
                    )
                    os.replace(target, failed)
                os.replace(retained, target)
            atomic_write(settings.env_file, old_env)
            atomic_write(settings.auth_file, old_auth)
            atomic_write(settings.env_file.with_name("active-compose.yaml"), old_compose)
            if old_secretary is not None:
                atomic_write(settings.secretary_file, old_secretary)
            else:
                settings.secretary_file.unlink(missing_ok=True)
            runtime.start()
            runtime.ready()
        raise
    finally:
        for temporary in temporary_roots:
            shutil.rmtree(temporary)
    return {name: str(path) for name, path in displaced.items()}


def build(runtime):
    revision = runtime.revision()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise OperationError("A full Git revision is required.")
    image = "agent-council-hermes:" + revision
    existing = runtime.run(
        [
            "docker",
            "image",
            "ls",
            "--quiet",
            "--no-trunc",
            image,
        ]
    )
    if existing:
        metadata = json.loads(runtime.run(["docker", "image", "inspect", image]))[0]
        if (
            metadata.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision")
            != revision
        ):
            raise OperationError("The existing release tag belongs to a different revision.")
        return revision, image
    # No live process is stopped until the immutable candidate is built.
    runtime.run(
        [
            "docker",
            "build",
            "--tag",
            image,
            "--build-arg",
            "COUNCIL_REVISION=" + revision,
            ".",
        ],
        timeout=1200,
    )
    return revision, image


def deploy(runtime, *, instruction_source=None, instruction_revision=None):
    files = None
    capabilities = []
    if instruction_source is not None or instruction_revision is not None:
        from .instructions import (
            checkout,
            collect,
            install_files,
            previous_files,
            source_requirements,
            validate_existing,
            verify_capabilities,
        )

        if instruction_source is None or instruction_revision is None:
            raise OperationError("Select both a private instruction source and reviewed revision.")
        instruction_source = instruction_source.absolute()
        checkout(runtime, instruction_source, instruction_revision)
        files = collect(instruction_source, runtime.settings.data_dir)
        capabilities = source_requirements(instruction_source)
        validate_existing(runtime.settings.data_dir, files)

        def secretary(names):
            return any(name.startswith(".council/secretary/instructions/") for name in names)

        if secretary(files) != secretary(previous_files(runtime.settings.data_dir)):
            raise OperationError(
                "Apply secretary role/source changes separately before a combined release."
            )
        if (
            secretary(files)
            and read_settings(runtime.settings.secretary_file).get(
                "COUNCIL_SECRETARY_INSTRUCTIONS", "image"
            )
            != "profile"
        ):
            raise OperationError("Apply the private secretary selector before a combined release.")
    revision, image = build(runtime)
    if files is not None:
        verify_capabilities(runtime, capabilities, image=image)
    backup = snapshot(runtime, restart=False)
    switched = False
    try:
        smoke(runtime, backup)  # Prove the previous image is usable before attempting an upgrade.
        uid, gid = identity(runtime.settings)
        prepare_copy = (
            (
                lambda home: install_files(
                    home, files, instruction_revision, uid, gid, capabilities=capabilities
                )
            )
            if files is not None
            else None
        )
        smoke(runtime, backup, image=image, prepare_copy=prepare_copy)
        switched = True
        if files is not None:
            install_files(
                runtime.settings.data_dir,
                files,
                instruction_revision,
                uid,
                gid,
                capabilities=capabilities,
            )
        runtime.settings.select_image(image)
        runtime.activate_source(runtime.settings.repository / "compose.yaml")
        runtime.start()
        runtime.ready()
        runtime.check_protected()
    except Exception:
        if switched:
            restore(runtime, backup, require_idle=False)
            raise OperationError(
                "Deployment failed; the previous image and state were restored."
            ) from None
        runtime.start()
        runtime.ready()
        raise OperationError(
            "Candidate verification failed; the previous service was restarted unchanged."
        ) from None
    atomic_write(runtime.settings.env_file.with_name("revision"), (revision + "\n").encode())
    result = {"revision": revision, "image": image, "backup": str(backup)}
    if files is not None:
        from .instructions import clear_failure

        clear_failure(runtime.settings)
        result["instructions_revision"] = instruction_revision
    return result
