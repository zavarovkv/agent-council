#!/usr/bin/env python3
"""Rehearse real recovery on a disposable CI host; refuse an existing deployment."""

import contextlib
import hashlib
import http.client
import json
import os
import pwd
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from council_ops.config import OperationError, Settings, atomic_write
from council_ops.install import recover
from council_ops.runtime import Runtime
from council_ops.snapshots import snapshot
from secretary_worker.history import History
from secretary_worker.protocol import ReplyRequest
from telegram_auth.sessions import SessionStore
from telegram_secretary.store import Store


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    if (
        os.geteuid() != 0
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("COUNCIL_RECOVERY_DRILL") != "fixture-only"
    ):
        raise SystemExit("Run this fixture-only rehearsal as root on a disposable GitHub CI host.")
    repository = Path(__file__).resolve().parents[1]
    existing = subprocess.check_output(
        [
            "docker",
            "ps",
            "--all",
            "--quiet",
            "--filter",
            "label=com.docker.compose.project=agent-council",
        ],
        text=True,
    ).strip()
    if existing:
        raise SystemExit("Refusing to rehearse beside an existing Council deployment.")
    account_name = "council-fixture"
    try:
        account = pwd.getpwnam(account_name)
    except KeyError:
        subprocess.run(
            [
                "useradd",
                "--system",
                "--no-create-home",
                "--shell",
                "/usr/sbin/nologin",
                account_name,
            ],
            check=True,
        )
        account = pwd.getpwnam(account_name)
    image = os.environ.get("COUNCIL_RUNTIME_TEST_IMAGE", "agent-council-hermes:test")
    source_runtime = restored_runtime = None
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="council-fresh-host-") as temporary:
        root = Path(temporary)
        tls_port = free_port()
        origin = f"https://localhost:{tls_port}"
        compose = (repository / "compose.yaml").read_text()
        compose = compose.replace(
            "    driver: bridge\n", "    driver: bridge\n    internal: true\n"
        )

        def settings_at(name, uid, gid):
            directory = root / name
            directory.mkdir(mode=0o700)
            values = {
                "HERMES_ACCOUNT": account_name,
                "HERMES_UID": str(uid),
                "HERMES_GID": str(gid),
                "HERMES_DATA_DIR": str(directory / "state/hermes"),
                "HERMES_WORKSPACE_DIR": str(directory / "state/workspace"),
                "HERMES_AUTH_ENV_FILE": str(directory / "auth.env"),
                "HERMES_PUBLIC_URL": origin,
                "HERMES_HTTP_PORT": str(free_port()),
                "COUNCIL_HERMES_IMAGE": image,
                "COUNCIL_SECRETARY_ENABLED": "true",
                "COUNCIL_SECRETARY_ENV_FILE": str(directory / "secretary.env"),
            }
            atomic_write(
                directory / "compose.env",
                ("".join(f"{k}={v}\n" for k, v in values.items())).encode(),
            )
            atomic_write(directory / "active-compose.yaml", compose.encode())
            return Settings.load(repository, directory / "compose.env", root / "backups")

        source = settings_at("original", 10000, 10000)
        target = settings_at("replacement", account.pw_uid, account.pw_gid)
        for path in (source.data_dir, source.workspace_dir):
            path.mkdir(mode=0o700, parents=True)
        atomic_write(
            source.auth_file,
            b"HERMES_TELEGRAM_ALLOWED_ID=12345\nHERMES_TELEGRAM_CLIENT_ID=fixture-client\nHERMES_TELEGRAM_CLIENT_SECRET=fixture-secret\n",
        )
        configuration = {
            "dashboard": {
                "public_url": origin,
                "trusted_proxies": ["172.30.90.1", "127.0.0.1", "::1"],
            },
            "plugins": {
                "disabled": [
                    "dashboard_auth/basic",
                    "dashboard_auth/nous",
                    "dashboard_auth/self_hosted",
                    "basic",
                    "nous",
                    "self-hosted",
                ]
            },
        }
        atomic_write(source.data_dir / "config.yaml", json.dumps(configuration).encode())
        atomic_write(source.data_dir / "telegram-owner.json", b'{"telegram_id":12345}')
        worker = source.data_dir / ".council/secretary"
        worker.mkdir(mode=0o700, parents=True)
        atomic_write(
            worker / "config.yaml", b'{"model":{"provider":"fixture","default":"fixture"}}'
        )
        atomic_write(
            worker / ".council-secretary.json", b'{"schema":1,"managed_by":"agent-council"}'
        )
        shutil.copytree(repository / "instructions/secretary", worker / "instructions")
        managed = {
            str(p.relative_to(source.data_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (worker / "instructions").rglob("*")
            if p.is_file()
        }
        atomic_write(
            source.data_dir / ".council/instructions.json",
            json.dumps({"schema": 1, "revision": "a" * 40, "files": managed}).encode(),
        )
        token, _ = SessionStore(
            source.data_dir / "dashboard-sessions.sqlite3", "fixture-client"
        ).issue({"id": 12345, "sub": "fixture", "iss": "https://oauth.telegram.org"})
        history = History(worker)
        request = ReplyRequest.parse(
            {
                "request_id": "b" * 64,
                "conversation": "c" * 64,
                "text": "fixture turn",
                "route": "business",
            }
        )
        history.begin(request)
        history.complete(request.identifier, "fixture answer")
        history.acknowledge(request.identifier)
        for filename in ("queue.sqlite3", "public-queue.sqlite3"):
            Store(source.data_dir / "plugin-data/council-secretary" / filename)
        atomic_write(
            source.secretary_file,
            (
                "COUNCIL_SECRETARY_KEY="
                + "a" * 64
                + "\nCOUNCIL_SECRETARY_OWNER_ID=12345\nCOUNCIL_SECRETARY_INSTRUCTIONS=profile\nCOUNCIL_SECRETARY_PUBLIC_DMS=true\n"
            ).encode(),
        )
        atomic_write(source.workspace_dir / "report.md", b"Fixture recovery artifact\n")
        for directory in (source.data_dir, source.workspace_dir):
            for parent, dirs, files in os.walk(directory):
                os.chown(parent, 10000, 10000)
                for name in dirs + files:
                    os.chown(Path(parent) / name, 10000, 10000)
        source_runtime = Runtime(source)
        try:
            source_runtime.start()
            source_runtime.ready()
            backup = snapshot(source_runtime, restart=False, portable=True)
            source_runtime.compose("down", "--volumes", "--remove-orphans")
            source_runtime = None
            shutil.rmtree(source.data_dir)
            shutil.rmtree(source.workspace_dir)
            source.auth_file.unlink()
            source.secretary_file.unlink()
            restored_runtime = Runtime(target)
            result = recover(restored_runtime, backup)
            assert result["recovered"] is True
            assert target.data_dir.stat().st_uid == account.pw_uid
            assert (
                target.workspace_dir / "report.md"
            ).read_bytes() == b"Fixture recovery artifact\n"

            # Internal bridges deliberately omit host NAT publishing. The fixture
            # TLS proxy uses the private bridge; routing is tested separately.
            addresses = [
                value["IPAddress"]
                for value in restored_runtime.inspect()["NetworkSettings"]["Networks"].values()
                if value.get("IPAddress") == "172.30.90.2"
            ]
            assert len(addresses) == 1
            backend = addresses[0]

            class Proxy(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    connection = http.client.HTTPConnection(backend, 9119, timeout=10)
                    try:
                        headers = {"Host": f"localhost:{tls_port}", "X-Forwarded-Proto": "https"}
                        if self.headers.get("Cookie"):
                            headers["Cookie"] = self.headers["Cookie"]
                        connection.request("GET", self.path, headers=headers)
                        response = connection.getresponse()
                        body = response.read()
                        self.send_response(response.status)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(body)
                    finally:
                        connection.close()

            cert, key = root / "fixture.crt", root / "fixture.key"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "1",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                    "-subj",
                    "/CN=localhost",
                    "-addext",
                    "subjectAltName=DNS:localhost",
                ],
                check=True,
                capture_output=True,
            )
            server = ThreadingHTTPServer(("127.0.0.1", tls_port), Proxy)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:

                def get(path, cookie=""):
                    client = http.client.HTTPSConnection(
                        "localhost",
                        tls_port,
                        context=ssl.create_default_context(cafile=str(cert)),
                        timeout=15,
                    )
                    try:
                        client.request("GET", path, headers={"Cookie": cookie})
                        response = client.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        client.close()

                code, public = get("/api/status")
                assert code == 200 and set(public) == {
                    "auth_required",
                    "auth_providers",
                    "auth_flows",
                }
                assert get("/api/config")[0] == 401
                code, private = get("/api/status", "__Host-hermes_session_at=" + token)
                assert code == 200 and "profiles" in private
            finally:
                server.shutdown()
                server.server_close()
            print(
                json.dumps(
                    {
                        "fresh_host_recovery": True,
                        "different_uid": account.pw_uid != 10000,
                        "different_paths": True,
                        "verified_https": True,
                        "persisted_owner_session": True,
                        "private_instructions": True,
                        "external_agent_network": False,
                        "elapsed_seconds": round(time.monotonic() - started, 1),
                    }
                )
            )
        finally:
            for runtime in (restored_runtime, source_runtime):
                if runtime is not None:
                    with contextlib.suppress(OperationError):
                        runtime.compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
