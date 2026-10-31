#!/usr/bin/env python3
"""Boot real Linear services offline with disposable profiles and fixture keys only."""

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from council_ops.linear import prepare_files


def main():
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="council-linear-") as temporary:
        root = Path(temporary).resolve()
        uid, gid = (os.getuid(), os.getgid()) if os.getuid() else (10000, 10000)
        data = root / "data"
        data.mkdir(mode=0o700)
        plan = {
            "schema": 1,
            "owner": str(uuid.UUID(int=2)),
            "organization": str(uuid.UUID(int=1)),
            "teams": [str(uuid.UUID(int=3))],
            "origin": "https://fixture.example.test",
            "agents": ["qui-gon", "dexter"],
        }
        prepare_files(
            data, plan, {"provider": "openai-codex", "default": "gpt-5.4"}, repository, uid, gid
        )
        auth = root / "fixture.env"
        auth.write_text("HERMES_TELEGRAM_OWNER_USERNAME=fixture_owner\n")
        auth.chmod(0o600)
        services = ("linear-adapter", "linear-executor", "dexter-executor")
        override = root / "offline.yaml"
        override.write_text(
            "services:\n"
            + "".join(
                f"  {service}:\n    restart: 'no'\n    networks: !reset []\n    network_mode: none\n    ports: !reset []\n"
                for service in services
            )
        )
        environment = {
            **os.environ,
            "HERMES_AUTH_ENV_FILE": str(auth),
            "COUNCIL_SECRETARY_ENV_FILE": str(auth),
            "HERMES_DATA_DIR": str(data),
            "HERMES_WORKSPACE_DIR": str(root / "unused"),
            "HERMES_PUBLIC_URL": plan["origin"],
            "HERMES_UID": str(uid),
            "HERMES_GID": str(gid),
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
                capture_output=True,
                text=True,
                check=True,
                timeout=180,
            ).stdout.strip()

        probe = """
import json,os,time,urllib.request,urllib.error
def call(path,body=None,authorized=True,key=None):
    headers={'Content-Type':'application/json'}
    if authorized: headers['Authorization']='Bearer '+os.environ['API_SERVER_KEY']
    if key: headers['Idempotency-Key']=key
    r=urllib.request.Request('http://127.0.0.1:8642'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    return json.load(urllib.request.urlopen(r,timeout=10))
try: call('/v1/capabilities',authorized=False)
except urllib.error.HTTPError as e: assert e.code==401
else: raise AssertionError('Unauthenticated executor')
c=call('/v1/capabilities')
assert c['features']['runs_idempotency']['durable'] is True
inventory=call('/v1/toolsets')['data']
enabled={v['name'] for v in inventory if v['enabled']}
assert enabled=={'web','clarify','council-linear'}, enabled
tools={tool for v in inventory if v['enabled'] for tool in v['tools']}
assert {'linear_delegate','linear_child_tasks','clarify'}<=tools, tools
assert not {'terminal','read_file','execute_code','memory','delegate_task'}&tools
first=call('/v1/runs',{'session_id':'linear_offline_fixture','input':'Offline fixture. Reply with one word; do not call tools.'},key='fixture-admission')
second=call('/v1/runs',{'session_id':'linear_offline_fixture','input':'Offline fixture. Reply with one word; do not call tools.'},key='fixture-admission')
assert first['run_id']==second['run_id']
call('/v1/runs/'+first['run_id']+'/stop',{})
"""
        try:
            run("up", "-d", "--no-build", "--no-deps", "--wait", "--wait-timeout", "120", *services)
            for service in services:
                container = run("ps", "--quiet", service)
                inspected = json.loads(
                    subprocess.check_output(["docker", "inspect", container], text=True)
                )[0]
                controls = inspected["HostConfig"]
                assert inspected["Config"]["User"] == f"{uid}:{gid}"
                assert (
                    controls["ReadonlyRootfs"]
                    and not controls.get("CapAdd")
                    and controls["CapDrop"] == ["ALL"]
                )
                assert controls["NetworkMode"] == "none" and not controls.get("PortBindings")
                assert len(inspected["Mounts"]) == (1 if service == "linear-adapter" else 2)
                if service != "linear-adapter":
                    run("exec", "-T", service, "/opt/hermes/.venv/bin/python", "-c", probe)
            print(
                "Linear services: isolated non-root boot, private API, exact tool policy and durable run deduplication: OK"
            )
        except subprocess.CalledProcessError:
            # Every profile and key in this drill is a disposable fixture.
            print(run("logs", "--tail", "60", *services), file=sys.stderr)
            raise
        finally:
            subprocess.run(
                [*compose, "down", "--volumes", "--remove-orphans"],
                env=environment,
                capture_output=True,
                timeout=60,
            )


if __name__ == "__main__":
    main()
