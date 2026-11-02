"""Verify a profile provisioner on a copy, then activate with rollback."""

import json

from .config import OperationError
from .instructions import verify_capabilities
from .lifecycle import identity, restore, smoke
from .linear_state import maintenance
from .snapshots import snapshot


@maintenance
def configure(runtime, *, capability, script, result_key, probe):
    verify_capabilities(runtime, [capability])
    runtime.require_idle()
    backup = snapshot(runtime, automatic=True)
    uid, gid = identity(runtime.settings)

    def provision(home):
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
                f"type=bind,source={home},target=/opt/data",
                "--workdir",
                "/opt/hermes",
                "--entrypoint",
                "/opt/hermes/.venv/bin/python",
                runtime.image()[0],
                "/opt/agent-council/" + script,
            ]
        )
        value = json.loads(result)
        if value.get(result_key) is not True:
            raise OperationError("Profile configuration was not confirmed.")
        return value

    smoke(runtime, backup, prepare_copy=provision)
    runtime.require_idle()
    runtime.stop()
    try:
        result = provision(runtime.settings.data_dir)
        runtime.start()
        runtime.ready()
        runtime.compose(
            "exec",
            "-T",
            "--workdir",
            "/opt/hermes",
            "hermes",
            "/opt/hermes/.venv/bin/python",
            "-c",
            probe,
        )
        runtime.check_protected()
    except Exception:
        restore(runtime, backup, require_idle=False)
        raise OperationError("Profile activation failed; previous state restored.") from None
    return {**result, "backup": str(backup)}
