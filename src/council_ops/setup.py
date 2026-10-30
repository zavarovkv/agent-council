"""Interactive root-only backup configuration; never echo or accept secret arguments."""

import getpass

from .config import OperationError, atomic_write
from .restic import Restic, validate_r2


def configure_backups(runtime):
    env_file = runtime.settings.env_file.with_name("backup.env")
    password_file = runtime.settings.env_file.with_name("backup-password")
    print("Create a private dedicated R2 bucket and an Object Read & Write token restricted to it.")
    print(
        "Keep the restic recovery password in your password manager, separately from this server."
    )
    repository = input(
        "Repository (s3:https://ACCOUNT.r2.cloudflarestorage.com/BUCKET/production): "
    ).strip()
    values = {
        "RESTIC_REPOSITORY": repository,
        "AWS_ACCESS_KEY_ID": getpass.getpass("R2 Access Key ID (hidden): ").strip(),
        "AWS_SECRET_ACCESS_KEY": getpass.getpass("R2 Secret Access Key (hidden): ").strip(),
        "AWS_DEFAULT_REGION": "auto",
    }
    validate_r2(values)
    password = None
    for path in (env_file, password_file):
        if path.exists() and (
            path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077
        ):
            raise OperationError("Existing backup credentials must be regular root-only files.")
    if not password_file.exists():
        password = getpass.getpass(
            "Recovery password from your password manager (24+ characters, hidden): "
        )
        confirmation = getpass.getpass("Repeat recovery password (hidden): ")
        if (
            password != confirmation
            or len(password.encode()) < 24
            or any(ord(c) < 32 for c in password)
        ):
            raise OperationError(
                "Passwords must match and contain at least 24 bytes without control characters."
            )
    else:
        print(
            "The existing recovery password will be retained. Password rotation requires restic key management."
        )
    previous = env_file.read_bytes() if env_file.exists() else None
    try:
        if password is not None:
            atomic_write(password_file, (password + "\n").encode())
        atomic_write(
            env_file, "".join(f"{key}={value}\n" for key, value in values.items()).encode()
        )
        Restic(runtime)
    except Exception:
        if previous is not None:
            atomic_write(env_file, previous)
        else:
            env_file.unlink(missing_ok=True)
        if password is not None:
            password_file.unlink(missing_ok=True)
        raise
    return {
        "backup_credentials_saved": True,
        "recovery_password_replaced": False,
        "timer_enabled": False,
    }
