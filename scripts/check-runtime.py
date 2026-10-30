#!/usr/bin/env python3
"""Exercise the real Compose command with isolated data and no network or secrets."""

import os
import subprocess
import tempfile
from pathlib import Path


def main():
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="council-runtime-") as directory:
        temporary = Path(directory)
        auth = temporary / "fake.env"
        auth.write_text("HERMES_TELEGRAM_OWNER_USERNAME=fixture_owner\n", encoding="utf-8")
        override = temporary / "runtime.yaml"
        override.write_text(
            "services:\n"
            "  hermes:\n"
            '    restart: "no"\n'
            "    volumes: !reset []\n"
            "    ports: !reset []\n"
            "    networks: !reset []\n"
            "    network_mode: none\n"
            "    tmpfs:\n"
            "      - /opt/data:rw,nosuid,nodev,size=128m\n"
            "      - /workspace:rw,nosuid,nodev,size=32m\n",
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment.update(
            COUNCIL_SECRETARY_ENV_FILE=str(auth),
            HERMES_AUTH_ENV_FILE=str(auth),
            HERMES_PUBLIC_URL="https://fixture.example.test",
            HERMES_UID="10000",
            HERMES_GID="10000",
            HERMES_DATA_DIR=str(temporary / "unused-data"),
            HERMES_WORKSPACE_DIR=str(temporary / "unused-workspace"),
            COUNCIL_HERMES_IMAGE=os.environ.get(
                "COUNCIL_RUNTIME_TEST_IMAGE", "agent-council-hermes:test"
            ),
        )
        compose = [
            "docker",
            "compose",
            "--project-name",
            temporary.name,
            "-f",
            str(repository / "compose.yaml"),
            "-f",
            str(override),
        ]

        def run(*arguments, **kwargs):
            return subprocess.run(
                [*compose, *arguments], env=environment, cwd=repository, timeout=180, **kwargs
            )

        try:
            run("up", "-d", "--no-build", "--wait", "--wait-timeout", "120", check=True)
            run(
                "exec",
                "-T",
                "--user",
                "hermes",
                "hermes",
                "hermes",
                "gateway",
                "ensure",
                "--timeout",
                "60",
                check=True,
            )
            run(
                "exec",
                "-T",
                "--user",
                "hermes",
                "hermes",
                "python3",
                "-c",
                "import json,urllib.error,urllib.request; "
                "r=urllib.request.Request('http://127.0.0.1:9119/api/config', "
                "headers={'Host':'fixture.example.test'}); "
                "s=urllib.request.urlopen('http://127.0.0.1:9119/api/status',timeout=5); "
                "assert json.load(s)['auth_required'] is True; "
                "\ntry: urllib.request.urlopen(r,timeout=5)\n"
                "except urllib.error.HTTPError as e: assert e.code == 401\n"
                "else: raise AssertionError('Dashboard API was not protected')\n",
                check=True,
            )
            print("Isolated Compose gateway readiness and dashboard authorization: OK")
        except subprocess.CalledProcessError:
            run("logs", "--no-color", "--tail", "60", check=False)
            raise
        finally:
            run("down", "--volumes", "--remove-orphans", check=False)


if __name__ == "__main__":
    main()
