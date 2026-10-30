#!/usr/bin/env python3
"""Exercise real encrypted backup and copied-profile recovery offline, with fake data."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from council_ops.config import Settings
from council_ops.lifecycle import smoke
from council_ops.runtime import Runtime
from council_ops.snapshots import load_manifest, snapshot


def main():
    repository = Path(__file__).resolve().parents[1]
    image = os.environ.get("COUNCIL_RUNTIME_TEST_IMAGE", "agent-council-hermes:test")
    uid, gid = (os.getuid(), os.getgid()) if os.getuid() else (10000, 10000)
    with tempfile.TemporaryDirectory(prefix="council-backups-") as temporary:
        root = Path(temporary).resolve()
        data, workspace = root / "hermes", root / "workspace"
        for path in (data, workspace):
            path.mkdir(mode=0o700)
            os.chown(path, uid, gid)
        auth = root / "auth.env"
        auth.write_text("HERMES_TELEGRAM_OWNER_USERNAME=fixture_owner\n")
        auth.chmod(0o600)
        env = root / "compose.env"
        env.write_text(
            f"HERMES_DATA_DIR={data}\nHERMES_WORKSPACE_DIR={workspace}\n"
            f"HERMES_AUTH_ENV_FILE={auth}\nHERMES_UID={uid}\nHERMES_GID={gid}\n"
            f"HERMES_PUBLIC_URL=https://fixture.example.test\nCOUNCIL_HERMES_IMAGE={image}\n"
        )
        env.chmod(0o600)
        settings = Settings.load(repository, env, root / "backups")

        class OfflineRuntime(Runtime):
            def inspect(self):
                return {"State": {"Running": False}}

            def require_idle(self):
                pass

            def running_services(self):
                return ["hermes"]

            def image(self):
                metadata = json.loads(self.run(["docker", "image", "inspect", image]))[0]
                return metadata["Id"], metadata["Config"].get("Labels", {}) or {}

        runtime = OfflineRuntime(settings)
        runtime.preflight()
        runtime.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                f"{uid}:{gid}",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=64m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--mount",
                f"type=bind,source={data},target=/opt/data",
                "-e",
                "HERMES_HOME=/opt/data",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "--entrypoint",
                "/opt/hermes/.venv/bin/python",
                image,
                "-c",
                "from pathlib import Path; from hermes_state import SessionDB; import sqlite3; "
                "p=Path('/opt/data'); (p/'config.yaml').write_text('{}\\n'); "
                "d=SessionDB(db_path=p/'state.db'); d.close(); "
                "c=sqlite3.connect(p/'state.db'); c.execute('CREATE TABLE recovery_marker(value TEXT)'); "
                "c.execute(\"INSERT INTO recovery_marker VALUES ('real encrypted round trip')\"); c.commit(); c.close()",
            ]
        )
        backup = snapshot(runtime, portable=True)
        smoke(runtime, backup)
        store, restored = root / "repository", root / "restored"
        store.mkdir(mode=0o700)
        restored.mkdir(mode=0o700)
        restic_env, password = root / "restic.env", root / "password"
        restic_env.write_text("RESTIC_REPOSITORY=/repository\n")
        password.write_text("fake-integration-password-never-use-in-production\n")
        for path in (restic_env, password):
            path.chmod(0o600)
        override = root / "offline.yaml"
        override.write_text(
            json.dumps(
                {
                    "services": {
                        "restic": {
                            "user": f"{os.getuid()}:{os.getgid()}",
                            "network_mode": "none",
                            "volumes": [
                                {
                                    "type": "bind",
                                    "source": str(store),
                                    "target": "/repository",
                                    "bind": {"create_host_path": False},
                                }
                            ],
                        }
                    }
                }
            )
        )
        environment = {
            **os.environ,
            "COUNCIL_BACKUP_ENV_FILE": str(restic_env),
            "COUNCIL_BACKUP_PASSWORD_FILE": str(password),
            "COUNCIL_SNAPSHOT_DIR": str(backup),
            "COUNCIL_RESTORE_DIR": str(restored),
        }
        compose = [
            "docker",
            "compose",
            "--project-name",
            root.name,
            "-f",
            str(repository / "compose.backup.yaml"),
            "-f",
            str(override),
        ]

        def restic(*arguments, expect_success=True):
            result = subprocess.run(
                [*compose, "run", "--rm", "--no-deps", "restic", *arguments],
                env=environment,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if (result.returncode == 0) != expect_success:
                raise RuntimeError("Offline restic integration failed; fixture credentials only.")
            return result.stdout

        try:
            restic("init")
            output = restic(
                "backup",
                "--json",
                "--host",
                "agent-council",
                "--tag",
                "application:agent-council",
                "/snapshot",
            )
            identifier = next(
                json.loads(line)["snapshot_id"]
                for line in output.splitlines()
                if line.startswith("{") and json.loads(line).get("message_type") == "summary"
            )
            if len(identifier) != 64:
                raise AssertionError("Restic must return an immutable full snapshot ID.")
            restic("check", "--read-data")
            password.write_text("wrong-password-that-cannot-decrypt-the-repository\n")
            restic("snapshots", expect_success=False)
            password.write_text("fake-integration-password-never-use-in-production\n")
            restic("restore", identifier, "--target", "/restore")
            recovered = restored / "snapshot"
            load_manifest(recovered)
            smoke(runtime, recovered)
            with sqlite3.connect(recovered / "hermes/state.db") as connection:
                if (
                    connection.execute("SELECT value FROM recovery_marker").fetchone()[0]
                    != "real encrypted round trip"
                ):
                    raise AssertionError("Recovered history differs from the backup.")
            print("Encrypted backup, wrong-password rejection and offline history recovery: OK")
        finally:
            subprocess.run(
                [*compose, "down", "--volumes", "--remove-orphans"],
                env=environment,
                capture_output=True,
                timeout=60,
            )


if __name__ == "__main__":
    main()
