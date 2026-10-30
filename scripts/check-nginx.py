#!/usr/bin/env python3
"""Exercise the real Nginx template against loopback-only, credential-free fixtures."""

import contextlib
import http.client
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        code = 503 if self.path == "/provider-failure" else 401
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"application response")


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    nginx = shutil.which("nginx")
    if nginx is None:
        raise SystemExit("Install Nginx to run this isolated routing check.")
    repository = Path(__file__).resolve().parents[1]
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="council-nginx-") as temporary:
            root = Path(temporary)
            root.chmod(0o755)  # Fixture content is public; allow Nginx's worker to traverse.
            first, second = port(), port()
            source = (repository / "config/nginx-council.conf").read_text()
            source = source.replace("listen 80;", f"listen 127.0.0.1:{first};")
            source = source.replace("listen 443 ssl;", f"listen 127.0.0.1:{second};")
            source = re.sub(r"^\s*listen \[::\]:.*;", "", source, flags=re.M)
            source = re.sub(r"^\s*ssl_\w+ .*;", "", source, flags=re.M)
            source = re.sub(r"^\s*(?:access_log|error_log) .*;", "", source, flags=re.M)
            source = source.replace("127.0.0.1:9119", f"127.0.0.1:{upstream.server_port}")
            source = source.replace("/var/www/hermes-status", str(root))
            source = source.replace("/var/www/hermes-acme", str(root))
            config = root / "nginx.conf"
            config.write_text(
                f"pid {root}/nginx.pid;\nerror_log {root}/error.log;\n"
                "events { worker_connections 64; }\nhttp { access_log off;\n" + source + "\n}\n"
            )
            subprocess.run([nginx, "-t", "-c", str(config)], check=True, capture_output=True)
            process = subprocess.Popen(
                [nginx, "-c", str(config), "-g", "daemon off;"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            def request(path):
                connection = http.client.HTTPConnection("127.0.0.1", second, timeout=3)
                try:
                    connection.request("GET", path, headers={"Host": "council.zavarov.com"})
                    response = connection.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    connection.close()

            deadline = time.monotonic() + 5
            while True:
                try:
                    response = request("/api/config")
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Fixture Nginx failed to start") from None
                    time.sleep(0.05)
            assert response[0] == 401 and response[2] == b"application response"
            assert request("/provider-failure")[0:3:2] == (503, b"application response")
            assert request("/__hermes_setup_pending")[0] == 404
            upstream.shutdown()
            upstream.server_close()
            code, headers, body = request("/profiles?profile=default")
            assert code == 503 and headers.get("Retry-After") == "30"
            assert headers.get("Cache-Control") == "no-store"
            assert "Agent Council" in body.decode()
            print("Nginx auth passthrough and retryable origin-failure response: OK")
    finally:
        upstream.shutdown()
        upstream.server_close()
        if process is not None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
