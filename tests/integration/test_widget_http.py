"""Real signed widget tokens, cookies, CSRF, replay and gate integration offline."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_cli.dashboard_auth.middleware import gated_auth_middleware
from hermes_cli.dashboard_auth.registry import register_global_provider, unregister_global_provider
from hermes_cli.dashboard_auth.routes import router as auth_router
from plugins.dashboard_auth.telegram_owner import register, widget
from plugins.dashboard_auth.telegram_owner.challenges import ChallengeStore


class WidgetHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.foreign = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = {
            "HERMES_HOME": str(self.home),
            "HERMES_PUBLIC_URL": "https://council.example.com",
            "HERMES_TELEGRAM_CLIENT_ID": "123456789",
            "HERMES_TELEGRAM_CLIENT_SECRET": "test-secret-only",
            "HERMES_TELEGRAM_OWNER_USERNAME": "test_owner",
            "HERMES_TELEGRAM_ALLOWED_ID": "",
            "HERMES_TELEGRAM_ALLOWED_IDS": "67890",
            "HERMES_TELEGRAM_SESSION_DAYS": "30",
        }
        patcher = patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        store_patch = patch.object(widget, "store", ChallengeStore())
        store_patch.start()
        self.addCleanup(store_patch.stop)
        self.provider = self.make_provider()
        register_global_provider(self.provider)
        self.addCleanup(lambda: unregister_global_provider(self.provider.name, self.provider))
        app = FastAPI()
        app.state.auth_required = True
        app.middleware("http")(gated_auth_middleware)
        app.include_router(auth_router)

        @app.get("/api/protected")
        def protected():
            return {"authorized": True}

        self.client = TestClient(app, base_url=environment["HERMES_PUBLIC_URL"])
        self.addCleanup(self.client.close)
        self.headers = {"Origin": environment["HERMES_PUBLIC_URL"], "Sec-Fetch-Site": "same-origin"}
        self.path = "/auth/login/telegram-widget"

    def make_provider(self):
        providers = []
        register(SimpleNamespace(register_dashboard_auth_provider=providers.append))
        provider = providers[0]
        provider._get_discovery = Mock(return_value={"issuer": "https://oauth.telegram.org"})
        provider._get_jwks_client = Mock(
            return_value=SimpleNamespace(
                get_signing_key_from_jwt=lambda token: SimpleNamespace(key=self.key.public_key())
            )
        )
        return provider

    def challenge(self, next_path="/"):
        response = self.client.post(
            self.path + "/challenge", json={"next": next_path}, headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("secret", response.text.lower())
        return response.json()["nonce"]

    def token(self, nonce, changes=None, key=None):
        now = int(time.time())
        claims = {
            "iss": "https://oauth.telegram.org",
            "aud": "123456789",
            "sub": "owner-account",
            "id": 12345,
            "preferred_username": "test_owner",
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
        }
        claims.update(changes or {})
        return jwt.encode(claims, key or self.key, algorithm="RS256")

    def complete(self, token):
        return self.client.post(
            self.path + "/complete", json={"id_token": token}, headers=self.headers
        )

    def test_widget_owner_login_sets_hardened_cookies_and_unlocks_gate(self):
        self.assertEqual(self.client.get("/api/protected").status_code, 401)
        token = self.token(self.challenge("/sessions?tab=recent"))
        response = self.complete(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["next"], "/sessions?tab=recent")
        self.assertNotIn(token, response.text)
        cookie = next(
            value
            for value in response.headers.get_list("set-cookie")
            if value.startswith("__Host-hermes_session_at=") and "Max-Age=0" not in value
        )
        for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
            self.assertIn(flag, cookie)
        self.assertEqual(self.client.get("/api/protected").status_code, 200)

    def test_session_outlives_telegram_token_and_survives_provider_restart(self):
        token = self.token(self.challenge())
        response = self.complete(token)
        self.assertEqual(response.status_code, 200)
        cookie = self.client.cookies.get("__Host-hermes_session_at")
        self.assertNotEqual(cookie, token)
        self.provider.session_store.clock = lambda: time.time() + 86400
        self.provider._get_jwks_client.side_effect = AssertionError("No IDP request after login")
        self.assertEqual(self.client.get("/api/protected").status_code, 200)
        restarted = self.make_provider()
        restarted.session_store.clock = lambda: time.time() + 86400
        self.assertIsNotNone(restarted.verify_session(access_token=cookie))
        self.assertGreater(restarted.verify_session(access_token=cookie).expires_at, time.time())
        headers = response.headers.get_list("set-cookie")
        session_cookie = next(
            h for h in headers if h.startswith("__Host-hermes_session_at=") and "Max-Age=0" not in h
        )
        self.assertRegex(session_cookie, r"Max-Age=259[12][0-9]{3}")

    def test_logout_revokes_captured_application_session(self):
        self.assertEqual(self.complete(self.token(self.challenge())).status_code, 200)
        captured = self.client.cookies.get("__Host-hermes_session_at")
        response = self.client.post("/auth/logout", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.provider.verify_session(access_token=captured))
        self.assertEqual(self.client.get("/api/protected").status_code, 401)

    def test_expired_application_session_is_rejected_and_store_failure_keeps_cookie(self):
        self.assertEqual(self.complete(self.token(self.challenge())).status_code, 200)
        self.provider.session_store.clock = lambda: time.time() + 31 * 86400
        self.assertEqual(self.client.get("/api/protected").status_code, 401)
        self.provider.session_store.clock = time.time
        self.assertEqual(self.complete(self.token(self.challenge())).status_code, 200)
        self.provider.session_store.path.write_bytes(b"corrupted database")
        response = self.client.get("/api/protected")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("Max-Age=0", response.headers.get("set-cookie", ""))

    def test_invited_account_is_authorized_without_pinning_owner(self):
        response = self.complete(
            self.token(self.challenge(), {"id": 67890, "preferred_username": "guest"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse((self.home / "telegram-owner.json").exists())

    def test_removed_invitation_revokes_widget_session(self):
        self.complete(self.token(self.challenge(), {"id": 67890, "preferred_username": "guest"}))
        self.assertEqual(self.client.get("/api/protected").status_code, 200)
        with patch.dict(os.environ, {"HERMES_TELEGRAM_ALLOWED_IDS": ""}):
            self.provider = self.make_provider()
        register_global_provider(self.provider)
        self.assertEqual(self.client.get("/api/protected").status_code, 401)

    def test_wrong_nonce_signature_issuer_audience_and_expiry_never_enroll_owner(self):
        for changes, key in (
            ({"nonce": "wrong"}, None),
            ({}, self.foreign),
            ({"iss": "https://evil.example"}, None),
            ({"aud": "other"}, None),
            ({"exp": int(time.time()) - 120}, None),
        ):
            with self.subTest(changes=changes):
                response = self.complete(self.token(self.challenge(), changes, key))
                self.assertIn(response.status_code, (401, 503))
                self.assertFalse((self.home / "telegram-owner.json").exists())

    def test_verified_but_uninvited_account_is_denied(self):
        response = self.complete(
            self.token(self.challenge(), {"id": 99999, "preferred_username": "stranger"})
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/protected").status_code, 401)

    def test_result_cannot_be_replayed_even_with_captured_nonce(self):
        nonce = self.challenge()
        token = self.token(nonce)
        self.assertEqual(self.complete(token).status_code, 200)
        self.client.cookies.set(widget.NONCE_COOKIE, nonce, domain="council.example.com", path="/")
        self.assertEqual(self.complete(token).status_code, 403)

    def test_missing_or_foreign_browser_cookie_cannot_complete_login(self):
        nonce = self.challenge()
        self.challenge()
        self.assertEqual(self.complete(self.token(nonce)).status_code, 401)
        self.assertFalse((self.home / "telegram-owner.json").exists())
        self.client.cookies.clear()
        self.assertEqual(self.complete(self.token(nonce)).status_code, 400)

    def test_foreign_or_missing_origin_is_rejected_before_challenge(self):
        for headers in (
            {},
            {"Origin": "https://evil.example"},
            {**self.headers, "Sec-Fetch-Site": "cross-site"},
        ):
            with self.subTest(headers=headers):
                response = self.client.post(self.path + "/challenge", json={}, headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("set-cookie", response.headers)

    def test_oversized_body_and_external_redirect_are_rejected(self):
        response = self.client.post(
            self.path + "/challenge",
            content=b"x" * 17000,
            headers={**self.headers, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        for target in (
            "//evil.example",
            "https://evil.example",
            "/%5cevil.example",
            "/auth/logout",
        ):
            with self.subTest(target=target):
                response = self.complete(self.token(self.challenge(target)))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["next"], "/")
