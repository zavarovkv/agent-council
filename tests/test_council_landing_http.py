"""Exercise the Council HTTP smoke gate with a loopback-only fixture."""

import importlib.util
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify-council-landing.py"
spec = importlib.util.spec_from_file_location("council_http_check", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class Fixture(BaseHTTPRequestHandler):
    broken = False

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path in ("/", "/en/"):
            status = 503 if self.broken and self.path == "/en/" else 200
            body = b"Agent Council static landing"
        elif self.path == "/login?next=%2Fprofiles":
            status, body = 200, b"Login"
        else:
            status, body = 404, b"not found"
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)


class SmokeTests(unittest.TestCase):
    def setUp(self):
        Fixture.broken = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def test_accepts_valid_public_routes(self):
        checker.check(self.base)

    def test_rejects_broken_english_page(self):
        Fixture.broken = True
        with self.assertRaises(RuntimeError):
            checker.check(self.base)

    def test_rejects_invalid_url(self):
        with self.assertRaises(ValueError):
            checker.check("file:///etc/passwd")


if __name__ == "__main__":
    unittest.main()
