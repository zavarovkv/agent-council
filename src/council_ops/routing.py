"""Apply only Council's existing virtual host, validating and retaining rollback."""

import os
from pathlib import Path

from .config import OperationError, atomic_write


def configure(runtime, *, target=Path("/etc/nginx/sites-available/council")):
    revision = runtime.revision()
    source = runtime.settings.repository / "config/nginx-council.conf"
    if runtime.settings.values["HERMES_PUBLIC_URL"].rstrip("/") != "https://council.zavarov.com":
        raise OperationError("Review the hostname-specific Nginx template for this installation.")
    if (
        target.is_symlink()
        or not target.is_file()
        or target.stat().st_uid != os.geteuid()
        or target.stat().st_mode & 0o022
    ):
        raise OperationError("Expected Council's existing root-owned Nginx virtual host.")
    original = target.read_bytes()
    updated = source.read_bytes()
    if original == updated:
        return {"route_changed": False}
    retained = runtime.settings.env_file.with_name("nginx-council.previous.conf")
    atomic_write(retained, original)
    atomic_write(target, updated, mode=0o644)
    try:
        runtime.run(["nginx", "-t"])
        runtime.check_protected()
        runtime.run(["systemctl", "reload", "nginx"])
        runtime.check_protected()
    except Exception:
        atomic_write(target, original, mode=0o644)
        runtime.run(["nginx", "-t"])
        runtime.run(["systemctl", "reload", "nginx"])
        raise OperationError(
            "Council routing update failed; its previous route was restored."
        ) from None
    return {"route_changed": True, "graceful_reload": True, "source_revision": revision}
