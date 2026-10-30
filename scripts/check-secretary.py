#!/usr/bin/env python3
"""Start the actual isolated worker offline using fixture data and no provider grant."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="council-secretary-") as temporary:
        root = Path(temporary)
        home = root / "profile"
        home.mkdir(mode=0o700)
        uid, gid = (os.getuid(), os.getgid()) if os.getuid() else (10000, 10000)
        os.chown(home, uid, gid)
        shutil.copytree(repository / "instructions/secretary", home / "instructions")
        auth = root / "fixture.env"
        auth.write_text(
            "COUNCIL_SECRETARY_KEY="
            + "a" * 64
            + "\nCOUNCIL_SECRETARY_OWNER_ID=12345\nCOUNCIL_SECRETARY_INSTRUCTIONS=profile\n"
        )
        auth.chmod(0o600)
        override = root / "offline.yaml"
        override.write_text(
            "services:\n  secretary:\n    restart: 'no'\n    networks: !reset []\n"
            "    network_mode: none\n    volumes: !override\n"
            f"      - type: bind\n        source: {home}\n        target: /opt/data\n"
            "        bind:\n          create_host_path: false\n"
        )
        environment = {
            **os.environ,
            "COUNCIL_SECRETARY_ENV_FILE": str(auth),
            "HERMES_AUTH_ENV_FILE": str(auth),
            "HERMES_UID": str(uid),
            "HERMES_GID": str(gid),
            "HERMES_PUBLIC_URL": "https://fixture.example.test",
            "HERMES_DATA_DIR": str(root / "unused"),
            "HERMES_WORKSPACE_DIR": str(root / "unused-workspace"),
            "COUNCIL_HERMES_IMAGE": os.environ.get(
                "COUNCIL_RUNTIME_TEST_IMAGE", "agent-council-hermes:test"
            ),
        }
        compose = [
            "docker",
            "compose",
            "--project-name",
            root.name,
            "-f",
            str(repository / "compose.yaml"),
            "-f",
            str(override),
        ]

        def run(*arguments):
            return subprocess.run(
                [*compose, *arguments],
                env=environment,
                cwd=repository,
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            ).stdout.strip()

        try:
            run(
                "up", "-d", "--no-build", "--no-deps", "--wait", "--wait-timeout", "90", "secretary"
            )
            identifier = run("ps", "--quiet", "secretary")
            inspected = json.loads(
                subprocess.check_output(["docker", "inspect", identifier], text=True)
            )[0]
            controls = inspected["HostConfig"]
            assert inspected["Config"]["User"] == f"{uid}:{gid}"
            assert controls["ReadonlyRootfs"] and not controls.get("CapAdd")
            assert controls["CapDrop"] == ["ALL"] and controls["NetworkMode"] == "none"
            assert not controls.get("PortBindings")
            assert (
                len(inspected["Mounts"]) == 1
                and inspected["Mounts"][0]["Destination"] == "/opt/data"
            )
            run(
                "exec",
                "-T",
                "secretary",
                "/opt/hermes/.venv/bin/hermes",
                "plugins",
                "enable",
                "council-secretary",
            )
            run(
                "exec",
                "-T",
                "secretary",
                "/opt/hermes/.venv/bin/python",
                "-c",
                "import json,urllib.request,urllib.error; "
                "r=urllib.request.Request('http://127.0.0.1:9120/reply',data=b'{}',headers={'Content-Type':'application/json'}); "
                "\ntry: urllib.request.urlopen(r,timeout=3)\n"
                "except urllib.error.HTTPError as e: assert e.code==401\n"
                "else: raise AssertionError('worker accepted unauthenticated request')\n",
            )
            print(
                "Actual secretary service: non-root, read-only, isolated mount/network and machine auth: OK"
            )
        finally:
            subprocess.run(
                [*compose, "down", "--volumes", "--remove-orphans"],
                env=environment,
                capture_output=True,
                timeout=60,
            )


if __name__ == "__main__":
    main()
