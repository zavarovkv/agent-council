#!/usr/bin/env python3
"""Check real Compose parsing with fake data; never read production secrets."""

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

from env_utils import render_environment


def main():
    repository = Path(__file__).resolve().parents[1]
    literal = 'test$HOME${UNDEFINED} "quotes" \\ #literal'
    with tempfile.TemporaryDirectory() as directory:
        env_file = Path(directory) / "fake.env"
        env_file.write_text(
            render_environment({"HERMES_TELEGRAM_CLIENT_SECRET": literal}, raw=True),
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment.update(
            HERMES_AUTH_ENV_FILE=str(env_file),
            HERMES_PUBLIC_URL="https://council.example.com",
            COUNCIL_HERMES_IMAGE="agent-council-hermes:test",
        )
        # Exercise the original service's env_file directly. Compose versions
        # differ in their normalized `config` output, and that output also
        # escapes dollars. Only the container's actual environment is the contract.
        fixture = Path(directory) / "override.yaml"
        assertion = (
            "import base64,os; "
            "assert os.environ['HERMES_TELEGRAM_CLIENT_SECRET'] == "
            f"base64.b64decode('{base64.b64encode(literal.encode()).decode()}')"
            ".decode(), 'Compose changed a literal secret value'"
        )
        fixture.write_text(
            "services:\n"
            "  hermes:\n"
            '    entrypoint: ["/opt/hermes/.venv/bin/python", "-c"]\n'
            f"    command: {json.dumps([assertion])}\n"
            '    user: "10000:10000"\n'
            "    read_only: true\n"
            '    restart: "no"\n'
            "    volumes: !reset []\n"
            "    ports: !reset []\n"
            "    networks: !reset []\n"
            "    network_mode: none\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                "agent-council-env-contract",
                "-f",
                str(repository / "compose.yaml"),
                "-f",
                str(fixture),
                "run",
                "--rm",
                "--no-deps",
                "hermes",
            ],
            cwd=repository,
            env=environment,
            check=True,
        )
    print("Compose literal-secret contract: OK")


if __name__ == "__main__":
    main()
