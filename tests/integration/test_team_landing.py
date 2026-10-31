"""Verify public-route bounds inside the pinned application image."""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_cli.dashboard_auth.middleware import gated_auth_middleware
from hermes_cli.dashboard_auth.routes import router as auth_router
from plugins.dashboard_auth.telegram_owner import team


class TeamLandingTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.state.auth_required = True
        app.middleware("http")(gated_auth_middleware)
        app.include_router(auth_router)

        @app.get("/api/config")
        def private_configuration():
            return {"private": True}

        self.client = TestClient(app, follow_redirects=False)
        self.addCleanup(self.client.close)

    def test_public_snapshot_is_small_and_does_not_require_operator_cookies(self):
        with patch.object(team, "current", return_value={"schema": 1, "agents": []}):
            result = self.client.get("/auth/login/council/team")
        self.assertEqual(result.json(), {"schema": 1, "agents": []})
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertNotIn("set-cookie", result.headers)

    def test_only_fixed_image_owned_portraits_are_served(self):
        for slug in ("qui-gon", "dexter", "lando", "padme"):
            result = self.client.get("/auth/login/council/avatars/" + slug)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers["content-type"], "image/png")
        for path in ("auth.json", "..%2fauth.json", "padme.png", "qui-gon.env"):
            self.assertEqual(
                self.client.get("/auth/login/council/avatars/" + path).status_code, 404
            )

    def test_public_routes_do_not_unlock_private_configuration(self):
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            response = self.client.get("/api/config", headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn('"private":true', response.text)
