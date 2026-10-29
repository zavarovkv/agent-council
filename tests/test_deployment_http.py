"""Opt-in HTTPS smoke tests against the real, unauthenticated deployment."""

import http.client
import json
import os
import unittest
import urllib.error
import urllib.parse
import urllib.request

LIVE_URL = os.environ.get("HERMES_LIVE_URL", "").rstrip("/")


@unittest.skipUnless(LIVE_URL, "Set HERMES_LIVE_URL to run HTTPS deployment checks.")
class DeploymentHTTPTests(unittest.TestCase):
    def request(self, path, headers=None):
        request = urllib.request.Request(LIVE_URL + path, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_only_telegram_owner_provider_is_enabled(self):
        status, body = self.request("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIs(data["auth_required"], True)
        self.assertEqual(data["auth_providers"], ["telegram-owner"])

    def test_setup_login_page_is_served_as_html(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, response, code, message, headers, url):
                return None

        opener = urllib.request.build_opener(NoRedirect())
        try:
            response = opener.open(
                LIVE_URL + "/auth/login?provider=telegram-owner&next=%2F", timeout=15
            )
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.code in (302, 303, 307):
                self.skipTest("Telegram login is already configured and redirects to the provider.")
            self.assertEqual(response.code, 503)
            self.assertEqual(response.headers.get_content_type(), "text/html")
            self.assertEqual(response.headers.get_content_charset(), "utf-8")
            self.assertIn("Вход скоро будет доступен", response.read().decode("utf-8"))

    def test_private_api_requires_authorization(self):
        for path in ("/api/config", "/api/env", "/api/sessions", "/api/skills", "/api/mcp"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 401)

    def test_forged_credentials_do_not_unlock_configuration(self):
        status, _ = self.request(
            "/api/config",
            {
                "Authorization": "Bearer invalid",
                "Cookie": "hermes_session=invalid; hermes_dashboard_session=invalid",
                "X-Forwarded-User": "zavarov",
                "X-Auth-Request-User": "zavarov",
            },
        )
        self.assertIn(status, (401, 403))

    def test_chat_websocket_is_not_accessible_without_a_session(self):
        # urllib forces Connection: close; use a genuine Upgrade handshake here.
        parsed = urllib.parse.urlsplit(LIVE_URL)
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=15)
        try:
            connection.request(
                "GET",
                parsed.path + "/api/pty",
                headers={
                    "Upgrade": "websocket",
                    "Connection": "Upgrade",
                    "Sec-WebSocket-Version": "13",
                    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
                    "Origin": LIVE_URL,
                },
            )
            response = connection.getresponse()
            self.assertIn(response.status, (401, 403))
        finally:
            connection.close()
