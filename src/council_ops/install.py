"""Initialize a new single-host deployment without choosing an owner or touching Nginx."""

import json
import os
import pwd

from .config import OperationError, atomic_write
from .lifecycle import build, identity, smoke
from .snapshots import copy_state, load_manifest


def check_new_host(runtime):
    settings = runtime.settings
    try:
        account = pwd.getpwnam(settings.values.get("HERMES_ACCOUNT", "hermes"))
    except KeyError:
        raise OperationError(
            "Create the dedicated HERMES_ACCOUNT and set its UID/GID first."
        ) from None
    uid, gid = identity(settings)
    if (account.pw_uid, account.pw_gid) != (uid, gid):
        raise OperationError("HERMES_UID/GID must match the dedicated host account.")
    for path in (settings.data_dir, settings.workspace_dir):
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise OperationError("Installation requires new or empty runtime directories.")
    if runtime.run(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            "label=com.docker.compose.project=agent-council",
            "--filter",
            "label=com.docker.compose.service=hermes",
            "--format",
            "{{.ID}}",
        ]
    ):
        raise OperationError("Hermes is already installed; use deploy or restore.")


def install(runtime):
    settings = runtime.settings
    check_new_host(runtime)
    uid, gid = identity(settings)
    revision, image = build(runtime)
    for path in (settings.data_dir, settings.workspace_dir):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
        os.chown(path, uid, gid)
    settings.auth_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not settings.auth_file.exists():
        atomic_write(
            settings.auth_file, b"# Complete interactive Telegram Login setup; no default owner.\n"
        )
        atomic_write(settings.auth_file.with_suffix(".env.format"), b"raw\n")
    config = {
        "dashboard": {
            "public_url": settings.values["HERMES_PUBLIC_URL"],
            "trusted_proxies": ["172.30.90.1", "127.0.0.1", "::1"],
        },
        "terminal": {"backend": "local", "cwd": "/workspace"},
        "plugins": {
            "disabled": [
                "dashboard_auth/basic",
                "dashboard_auth/nous",
                "dashboard_auth/self_hosted",
                "basic",
                "nous",
                "self-hosted",
            ]
        },
    }
    profile_config = settings.data_dir / "config.yaml"
    atomic_write(profile_config, (json.dumps(config, indent=2) + "\n").encode())
    os.chown(profile_config, uid, gid)
    settings.select_image(image)
    runtime.activate_source(settings.repository / "compose.yaml")
    runtime.compose("config", "--quiet")
    runtime.start()
    runtime.ready()
    runtime.check_protected()
    atomic_write(settings.env_file.with_name("revision"), (revision + "\n").encode())
    atomic_write(settings.auth_file.with_name("deployment-kind"), b"docker\n")
    return {
        "installed": True,
        "revision": revision,
        "image": image,
        "default_owner_assigned": False,
    }


def recover(runtime, backup, *, rebuild=False):
    """Recover onto a prepared replacement host without building a new runtime image."""
    check_new_host(runtime)
    settings = runtime.settings
    backup, manifest = load_manifest(backup)
    image = manifest["image_id"]
    if rebuild:
        if runtime.revision() != manifest.get("revision"):
            raise OperationError(
                "To rebuild, check out the exact Git revision recorded by the backup first."
            )
        _, image = build(runtime)
    smoke(runtime, backup, image=image)
    uid, gid = identity(settings)
    for name, target in (("hermes", settings.data_dir), ("workspace", settings.workspace_dir)):
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.exists():
            target.rmdir()  # check_new_host proved this is an empty directory.
        copy_state(backup / name, target, uid=uid, gid=gid)
    settings.auth_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(settings.auth_file, (backup / "auth.env").read_bytes())
    atomic_write(settings.auth_file.with_suffix(".env.format"), b"raw\n")
    if "secretary.env" in manifest["files"]:
        atomic_write(settings.secretary_file, (backup / "secretary.env").read_bytes())
    settings.update_values(
        {
            "COUNCIL_HERMES_IMAGE": image,
            "COUNCIL_SECRETARY_ENABLED": "true"
            if "secretary" in manifest.get("running_services", [])
            else "false",
            "COUNCIL_LINEAR_ENABLED": "true"
            if "linear-adapter" in manifest.get("running_services", [])
            else "false",
            "COUNCIL_LINEAR_DEXTER_ENABLED": "true"
            if "dexter-executor" in manifest.get("running_services", [])
            else "false",
            "COUNCIL_LINEAR_MARKETING_ENABLED": "true"
            if "marketing-executor" in manifest.get("running_services", [])
            else "false",
        }
    )
    runtime.activate_source(backup / "compose.yaml")
    runtime.start()
    runtime.ready()
    runtime.check_protected()
    if manifest.get("revision"):
        atomic_write(
            settings.env_file.with_name("revision"), (manifest["revision"] + "\n").encode()
        )
    atomic_write(settings.auth_file.with_name("deployment-kind"), b"docker\n")
    return {"recovered": True, "image": image, "source_backup_retained": str(backup)}
