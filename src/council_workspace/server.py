"""Internal authenticated listener owned by the main Hermes gateway process."""

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .google import Google
from .policy import server_policy
from .service import Workspace

logger = logging.getLogger(__name__)
_server = None
_startup_lock = threading.Lock()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, workspace):
        self.workspace = workspace
        self.google = Google(workspace.home)
        self.capacity = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.capacity.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, address)

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.capacity.release()

    def handle_error(self, *_args):
        pass


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(75)

    def log_message(self, *_args):
        pass

    def respond(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        if len(body) > 196608:
            status, body = 413, b'{"error":"Use a smaller page or file chunk."}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            if self.path not in {"/workspace", "/google"}:
                self.respond(404, {"error": "not_found"})
                return
            policy = server_policy(self.server.workspace.home)
            received = self.headers.get("Authorization", "").encode()
            client = next(
                (
                    name
                    for name, value in policy["clients"].items()
                    if hmac.compare_digest(received, ("Bearer " + value["key"]).encode())
                ),
                None,
            )
            if client is None or len(self.headers.get_all("Authorization") or []) != 1:
                self.respond(401, {"error": "unauthorized"})
                return
            length = self.headers.get("Content-Length", "")
            if (
                self.headers.get("Transfer-Encoding")
                or not length.isdigit()
                or not 0 < int(length) <= 131072
                or self.headers.get_content_type() != "application/json"
            ):
                raise ValueError("Use bounded JSON.")
            body = self.rfile.read(int(length))
            if len(body) != int(length):
                raise ValueError("Incomplete request.")
            executor = self.server.google if self.path == "/google" else self.server.workspace
            result = executor.execute(client, json.loads(body))
            self.respond(200, result)
        except PermissionError:
            self.respond(
                403,
                {
                    "error": "Access is not authorized for this agent or the Google grant lacks the requested scope."
                },
            )
        except BlockingIOError:
            self.respond(429, {"error": "Workspace busy; retry with the same write key."})
        except ValueError as error:
            self.respond(400, {"error": str(error)[:500]})
        except Exception:
            self.respond(
                503,
                {
                    "error": "Cloud operation was not confirmed. Reuse the same key; inspect the material before repeating a write."
                },
            )


def start(home=None):
    global _server
    if home is None:
        from hermes_constants import get_default_hermes_root, get_hermes_home

        home = get_hermes_home().resolve()
        if home != get_default_hermes_root().resolve():
            raise PermissionError("Only the main gateway owns cloud credentials.")
    home = Path(home)
    server_policy(home)
    with _startup_lock:
        if _server is None:
            _server = Server(("0.0.0.0", 9123), Workspace(home))
            threading.Thread(
                target=_server.serve_forever, name="council-workspace", daemon=True
            ).start()
            logger.info(
                "Shared workspace listener started; provider credentials remain in the owner profile."
            )
