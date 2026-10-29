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
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(repository / "compose.yaml"),
                "config",
                "--no-env-resolution",
                "--format",
                "json",
            ],
            cwd=repository,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        config = json.loads(result.stdout)
        service = config["services"]["hermes"]
        # `compose config` escapes dollars for round-tripping. Check the actual
        # container environment instead, using only the real env_file definition.
        fixture = Path(directory) / "compose.json"
        fixture.write_text(
            json.dumps(
                {
                    "name": "agent-council-env-contract",
                    "services": {
                        "check": {
                            "image": service["image"],
                            "env_file": service["env_file"],
                            "network_mode": "none",
                            "read_only": True,
                            "user": "10000:10000",
                            "entrypoint": ["/opt/hermes/.venv/bin/python", "-c"],
                            "command": [
                                "import base64,os; "
                                "assert os.environ['HERMES_TELEGRAM_CLIENT_SECRET'] == "
                                f"base64.b64decode('{base64.b64encode(literal.encode()).decode()}')"
                                ".decode(), 'Compose changed a literal secret value'"
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(
            ["docker", "compose", "-f", str(fixture), "run", "--rm", "--no-deps", "check"],
            cwd=repository,
            check=True,
        )
    print("Compose literal-secret contract: OK")


if __name__ == "__main__":
    main()
