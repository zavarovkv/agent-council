"""Host entry points for the coordinator's narrow native research integration."""

import json

from .config import OperationError, atomic_write
from .lifecycle import identity
from .snapshots import snapshot


def configure(runtime):
    runtime.require_idle()
    backup = snapshot(runtime, automatic=True)
    uid, gid = identity(runtime.settings)
    result = runtime.run(
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
            f"type=bind,source={runtime.settings.data_dir},target=/opt/data",
            "--mount",
            f"type=bind,source={runtime.settings.repository / 'scripts/provision-research-workflow.py'},target=/tmp/provision-research.py,readonly",
            "--entrypoint",
            "/opt/hermes/.venv/bin/python",
            runtime.image()[0],
            "/tmp/provision-research.py",
        ]
    )
    value = json.loads(result)
    if value.get("research_configured") is not True:
        raise OperationError("Research configuration was not confirmed.")
    atomic_write(
        runtime.settings.env_file.with_name("research.json"), b'{"schema":1,"board":"research"}\n'
    )
    return {**value, "backup": str(backup), "restart_required": True}


def flush(runtime):
    if not runtime.settings.env_file.with_name("research.json").is_file():
        raise OperationError("Configure research filing before enabling its timer.")
    runtime.require_idle()
    result = json.loads(
        runtime.compose(
            "exec",
            "-T",
            "--user",
            "hermes",
            "hermes",
            "hermes",
            "research-filing",
            "flush",
            timeout=330,
        )
    )
    if (
        not isinstance(result, dict)
        or set(result)
        != {
            "completed",
            "pending_or_conflicted",
            "checked_at",
        }
        or any(type(value) is not int or value < 0 for value in result.values())
    ):
        raise OperationError("Unexpected research filing status.")
    return result
