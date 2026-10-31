"""Opt-in HTTPS smoke tests against the real, unauthenticated deployment."""

import http.client
import ipaddress
import json
import os
import socket
import ssl
import unittest
import urllib.parse

LIVE_URL = os.environ.get("HERMES_LIVE_URL", "").rstrip("/")
ORIGIN_IP = os.environ.get("HERMES_LIVE_ORIGIN_IP", "")


class VerifiedOriginConnection(http.client.HTTPSConnection):
    def connect(self):
        if not ORIGIN_IP:
            return super().connect()
        # Override only routing. Certificate verification and TLS SNI still use
        # the public hostname; the origin certificate must be valid for it.
        ipaddress.ip_address(ORIGIN_IP)
        connection = socket.create_connection((ORIGIN_IP, self.port), timeout=self.timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(
                connection, server_hostname=self.host
            )
        except BaseException:
            connection.close()
            raise


@unittest.skipUnless(LIVE_URL, "Set HERMES_LIVE_URL to run HTTPS deployment checks.")
class DeploymentHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = urllib.parse.urlsplit(LIVE_URL)
        if cls.url.scheme != "https" or not cls.url.hostname:
            raise ValueError("HERMES_LIVE_URL must be an HTTPS URL.")

    def connection(self):
        return VerifiedOriginConnection(self.url.hostname, self.url.port or 443, timeout=15)

    def request(self, path, headers=None):
        connection = self.connection()
        try:
            connection.request(
                "GET",
                self.url.path + path,
                headers={"User-Agent": "AgentCouncilHealth/1.0", **(headers or {})},
            )
            response = connection.getresponse()
            return response.status, response.read(), response.headers
        finally:
            connection.close()

    def test_only_telegram_owner_provider_is_enabled(self):
        status, body, _ = self.request("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIs(data["auth_required"], True)
        self.assertEqual(data["auth_providers"], ["telegram-owner"])
        self.assertEqual(set(data), {"auth_required", "auth_providers", "auth_flows"})

    def test_public_homepage_is_indexable_and_private_routes_are_not(self):
        code, body, headers = self.request("/")
        self.assertEqual(code, 200)
        self.assertIn('rel="canonical" href="https://council.zavarov.com/"', body.decode())
        self.assertIn('href="https://t.me/Qui_Gon_Jinn_ai"', body.decode())
        self.assertTrue(headers["X-Robots-Tag"].startswith("index, follow"))
        for path in ("/api/config", "/profiles"):
            self.assertIn("noindex", self.request(path)[2]["X-Robots-Tag"])
        code, sitemap, _ = self.request("/sitemap.xml")
        self.assertEqual(code, 200)
        self.assertIn(b"<loc>https://council.zavarov.com/</loc>", sitemap)
        code, robots, _ = self.request("/robots.txt")
        self.assertEqual(code, 200)
        self.assertIn(b"Sitemap: https://council.zavarov.com/sitemap.xml", robots)

    def test_setup_login_page_is_served_as_html(self):
        status, body, headers = self.request("/auth/login?provider=telegram-owner&next=%2F")
        if status in (302, 303, 307):
            target = urllib.parse.urlsplit(headers["Location"])
            self.assertEqual(target.scheme, "https")
            self.assertEqual(target.hostname, "oauth.telegram.org")
            return
        self.assertEqual(status, 503)
        self.assertEqual(headers.get_content_type(), "text/html")
        self.assertEqual(headers.get_content_charset(), "utf-8")
        self.assertIn("Вход скоро будет доступен", body.decode("utf-8"))

    def test_private_api_requires_authorization(self):
        for path in ("/api/config", "/api/env", "/api/sessions", "/api/skills", "/api/mcp"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 401)

    def test_public_team_and_portraits_pass_the_real_auth_gate(self):
        status, body, headers = self.request("/auth/login/council/team")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        data = json.loads(body)
        self.assertEqual(set(data), {"schema", "agents"})
        self.assertEqual(data["schema"], 1)
        self.assertEqual(
            {agent["slug"] for agent in data["agents"]},
            {"qui-gon", "dexter", "lando", "padme"},
        )
        for slug in ("qui-gon", "dexter", "lando", "padme"):
            with self.subTest(slug=slug):
                code, portrait, metadata = self.request("/auth/login/council/avatars/" + slug)
                self.assertEqual(code, 200)
                self.assertEqual(metadata.get_content_type(), "image/png")
                self.assertEqual(portrait[:8], b"\x89PNG\r\n\x1a\n")

    def test_forged_credentials_do_not_unlock_configuration(self):
        status, _, _ = self.request(
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
        connection = self.connection()
        try:
            connection.request(
                "GET",
                self.url.path + "/api/pty",
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
