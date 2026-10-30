"""Prepare an isolated model profile without cloning owner credentials or sessions."""

import json
import os
import re
import secrets

from .config import OperationError, atomic_write, read_settings
from .lifecycle import identity


def prepare(runtime, *, owner_name, assistant_name):
    for value in (owner_name, assistant_name):
        if (
            not 1 <= len(value) <= 100
            or value != value.strip()
            or any(ord(c) < 32 or c in "\"'`$" for c in value)
        ):
            raise OperationError(
                "Use a short literal display name without control/quoting characters."
            )
    settings = runtime.settings
    runtime.require_idle()
    # Deploy the compatible image first, so a rollback never needs an absent plugin.
    probe = "from pathlib import Path; assert Path('/opt/hermes/plugins/council-secretary/plugin.yaml').is_file()"
    runtime.compose(
        "exec", "-T", "--user", "hermes", "hermes", "/opt/hermes/.venv/bin/python", "-c", probe
    )
    pin = settings.data_dir / "telegram-owner.json"
    if pin.is_symlink():
        raise OperationError("Refusing a symlinked owner pin.")
    try:
        owner = json.loads(pin.read_text())["telegram_id"]
    except (OSError, ValueError, KeyError):
        raise OperationError(
            "Sign into the dashboard as its verified owner before enabling a secretary."
        ) from None
    if type(owner) is not int or not 0 < owner < 2**63:
        raise OperationError("A valid immutable numeric owner pin is required.")
    profile = settings.data_dir / ".council/secretary"
    marker = profile / ".council-secretary.json"
    if profile.resolve() != profile or marker.is_symlink():
        raise OperationError("Refusing a symlinked secretary profile.")
    if profile.exists() and any(profile.iterdir()) and not marker.is_file():
        raise OperationError("The secretary profile is unmanaged; refusing to overwrite it.")
    if marker.exists() and json.loads(marker.read_text()) != {
        "schema": 1,
        "managed_by": "agent-council",
    }:
        raise OperationError("Unrecognized secretary profile marker.")
    if settings.secretary_file.exists():
        values = read_settings(settings.secretary_file)
        if values.get("COUNCIL_SECRETARY_OWNER_ID") != str(owner) or not re.fullmatch(
            r"[a-f0-9]{64}", values.get("COUNCIL_SECRETARY_KEY", "")
        ):
            raise OperationError(
                "Existing secretary identity/key differs; handle rotation explicitly."
            )
    else:
        values = {
            "COUNCIL_SECRETARY_OWNER_ID": str(owner),
            "COUNCIL_SECRETARY_KEY": secrets.token_hex(32),
        }
    values.update(
        COUNCIL_SECRETARY_OWNER_NAME=owner_name, COUNCIL_SECRETARY_ASSISTANT_NAME=assistant_name
    )
    values.setdefault("COUNCIL_SECRETARY_CONTACT_IDS", "")
    values.setdefault("COUNCIL_SECRETARY_SPOUSE_USERNAME", "")
    values.setdefault("COUNCIL_SECRETARY_PUBLIC_DMS", "false")
    atomic_write(
        settings.secretary_file,
        "".join(f"{key}={value}\n" for key, value in values.items()).encode(),
    )
    uid, gid = identity(settings)
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(profile, uid, gid)
    if not marker.exists():
        source = (
            "import json,os; from pathlib import Path; from hermes_cli.config import read_user_config_raw; "
            "m=read_user_config_raw(Path(os.environ['HERMES_HOME'])/'config.yaml').get('model',{}); "
            "print(json.dumps({k:m.get(k,'') for k in ('provider','default')}))"
        )
        model = json.loads(
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
        config = {"model": model, "security": {"allow_lazy_installs": False}}
        atomic_write(profile / "config.yaml", (json.dumps(config, indent=2) + "\n").encode())
        atomic_write(marker, b'{"schema":1,"managed_by":"agent-council"}\n')
        for path in (profile / "config.yaml", marker):
            os.chown(path, uid, gid)
    runtime.compose(
        "exec",
        "-T",
        "--user",
        "hermes",
        "hermes",
        "hermes",
        "plugins",
        "enable",
        "council-secretary",
    )
    settings.update_values(
        {
            "COUNCIL_SECRETARY_ENABLED": "true",
            "COUNCIL_SECRETARY_ENV_FILE": str(settings.secretary_file),
        }
    )
    return {
        "prepared": True,
        "owner_credentials_copied": False,
        "model_login_required": True,
        "next_step": "deploy, then setup-model.sh secretary",
    }
