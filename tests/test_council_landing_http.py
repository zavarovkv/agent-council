"""Exercise the Council HTTP smoke gate with a loopback-only fixture."""

import hashlib
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
        elif self.path == "/api/config":
            status, body = (200 if self.broken else 401), b"API"
        else:
            status, body = 404, b"not found"
        self.send_response(status)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
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

    def test_rejects_old_cached_page_even_with_valid_marker(self):
        hashes = {"/": "0" * 64, "/en/": "1" * 64}
        with self.assertRaisesRegex(RuntimeError, "candidate artifact"):
            checker.check(self.base, expected_pages=hashes)
        actual = hashlib.sha256(b"Agent Council static landing").hexdigest()
        checker.check(self.base, expected_pages={"/": actual, "/en/": actual})

    def test_rejects_unverified_direct_origin(self):
        with self.assertRaises(ValueError):
            checker.check(self.base, origin_address="127.0.0.1")

    def test_rejects_invalid_url(self):
        with self.assertRaises(ValueError):
            checker.check("file:///etc/passwd")


if __name__ == "__main__":
    unittest.main()
