"""Verify public-route bounds inside the pinned application image."""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from plugins.dashboard_auth.telegram_owner import team


class TeamLandingTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(team.router)
        self.client = TestClient(app)

    def test_public_snapshot_is_small_and_does_not_require_operator_cookies(self):
        with patch.object(team, "current", return_value={"schema": 1, "agents": []}):
            result = self.client.get("/auth/council/team")
        self.assertEqual(result.json(), {"schema": 1, "agents": []})
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertNotIn("set-cookie", result.headers)

    def test_only_fixed_image_owned_portraits_are_served(self):
        for slug in ("qui-gon", "dexter", "lando", "padme"):
            result = self.client.get("/auth/council/avatars/" + slug)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers["content-type"], "image/png")
        for path in ("auth.json", "..%2fauth.json", "padme.png", "qui-gon.env"):
            self.assertEqual(self.client.get("/auth/council/avatars/" + path).status_code, 404)
