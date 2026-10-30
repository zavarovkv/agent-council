"""Bounded private HTTP transport for the secretary application service."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, service):
        self.service = service
        self.connections = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def handle_error(self, request, client_address):
        # Default socketserver diagnostics print tracebacks from arbitrary providers.
        # Keep even disconnect/parse failures free of caller or credential data.
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def respond(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"status": "ok", "tools": False})
        else:
            self.respond(404, {"error": "not_found"})

    def do_POST(self):
        if not self.server.service.authorized(self.headers.get("Authorization", "")):
            self.respond(401, {"error": "unauthorized"})
            return
        if self.path not in {"/reply", "/ack"}:
            self.respond(404, {"error": "not_found"})
            return
        length = self.headers.get("Content-Length", "")
        if (
            self.headers.get("Transfer-Encoding")
            or not length.isdigit()
            or not 0 < int(length) <= 30000
        ):
            self.respond(400, {"error": "invalid_length"})
            return
        if self.headers.get("Content-Type") != "application/json":
            self.respond(415, {"error": "json_required"})
            return
        try:
            body = self.rfile.read(int(length))
            if len(body) != int(length):
                raise ValueError("Incomplete request body.")
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("Object required")
            if self.path == "/ack":
                self.server.service.acknowledge(payload)
                answer = None
            else:
                answer = self.server.service.reply(payload)
        except BlockingIOError:
            self.respond(429, {"error": "busy_or_limited"})
        except (ValueError, TypeError):
            self.respond(400, {"error": "invalid_request"})
        except Exception:
            self.respond(503, {"error": "model_unavailable"})
        else:
            self.respond(200, {"reply": answer} if answer is not None else {"acknowledged": True})
