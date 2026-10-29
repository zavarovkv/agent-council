"""Exercise the real pinned Hermes/PyJWT adapter without network or credentials."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from hermes_cli.dashboard_auth import ProviderError
from hermes_cli.dashboard_auth.base import InvalidCodeError
from plugins.dashboard_auth.telegram_owner import register


class HermesAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.foreign_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name)
        self.environment = {
            "HERMES_HOME": str(self.profile),
            "HERMES_TELEGRAM_CLIENT_ID": "123456789",
            "HERMES_TELEGRAM_CLIENT_SECRET": "test-secret-not-used-for-exchange",
            "HERMES_TELEGRAM_OWNER_USERNAME": "test_owner",
            "HERMES_TELEGRAM_ALLOWED_ID": "",
            "HERMES_TELEGRAM_ALLOWED_IDS": "",
        }
        self.patcher = patch.dict(os.environ, self.environment)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.provider = self.make_provider()

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

    def token(self, changes=None, key=None):
        now = int(time.time())
        claims = {
            "iss": "https://oauth.telegram.org",
            "aud": "123456789",
            "sub": "test-account",
            "id": 12345,
            "preferred_username": "test_owner",
            "iat": now,
            "exp": now + 300,
        }
        claims.update(changes or {})
        return jwt.encode(claims, key or self.key, algorithm="RS256")

    def test_verified_owner_can_enroll(self):
        claims = self.provider._verify_id_token(self.token())
        self.assertEqual(claims["id"], 12345)
        self.assertTrue((self.profile / "telegram-owner.json").exists())

    def test_invalid_signature_never_enrolls_an_owner(self):
        with self.assertRaises(InvalidCodeError):
            self.provider._verify_id_token(self.token(key=self.foreign_key))
        self.assertFalse((self.profile / "telegram-owner.json").exists())

    def test_wrong_issuer_and_audience_never_enroll_an_owner(self):
        for changes in ({"iss": "https://attacker.example"}, {"aud": "another-client"}):
            with self.subTest(changes=changes), self.assertRaises(ProviderError):
                self.provider._verify_id_token(self.token(changes))
        self.assertFalse((self.profile / "telegram-owner.json").exists())

    def test_expired_token_never_enrolls_an_owner(self):
        with self.assertRaises(InvalidCodeError):
            self.provider._verify_id_token(self.token({"exp": int(time.time()) - 120}))
        self.assertFalse((self.profile / "telegram-owner.json").exists())

    def test_real_token_for_different_username_is_denied(self):
        with self.assertRaises(ProviderError):
            self.provider._verify_id_token(self.token({"preferred_username": "another_user"}))
        self.assertFalse((self.profile / "telegram-owner.json").exists())

    def test_per_request_alias_rechecks_the_pinned_owner(self):
        self.provider._verify_id_token(self.token())
        with self.assertRaises(ProviderError):
            self.provider._claims_for(self.token({"id": 99999}))

    def test_missing_owner_configuration_does_not_default_to_repository_owner(self):
        with patch.dict(os.environ, {"HERMES_TELEGRAM_OWNER_USERNAME": ""}):
            provider = self.make_provider()
        self.assertFalse(provider.ready)
        with self.assertRaises(ProviderError):
            provider.start_login(redirect_uri="https://council.example.com/auth/callback")
        with self.assertRaises(ProviderError):
            provider._verify_id_token(self.token())

    def test_explicit_numeric_id_does_not_require_a_username(self):
        with patch.dict(
            os.environ,
            {"HERMES_TELEGRAM_OWNER_USERNAME": "", "HERMES_TELEGRAM_ALLOWED_ID": "12345"},
        ):
            provider = self.make_provider()
        self.assertTrue(provider.ready)
        self.assertEqual(provider._verify_id_token(self.token())["id"], 12345)

    def test_invalid_owner_settings_keep_an_unavailable_provider_registered(self):
        for changes in (
            {"HERMES_TELEGRAM_ALLOWED_ID": "not-a-number"},
            {"HERMES_TELEGRAM_ALLOWED_ID": "0"},
            {"HERMES_TELEGRAM_OWNER_USERNAME": "invalid username"},
            {"HERMES_HOME": ""},
            {"HERMES_TELEGRAM_ALLOWED_IDS": "12345,,67890"},
        ):
            with self.subTest(changes=changes), patch.dict(os.environ, changes):
                provider = self.make_provider()
                self.assertEqual(provider.name, "telegram-owner")
                self.assertFalse(provider.ready)
                with self.assertRaises(ProviderError):
                    provider._claims_for(self.token())

    def test_verified_invited_account_can_enter_without_enrolling_the_owner(self):
        with patch.dict(os.environ, {"HERMES_TELEGRAM_ALLOWED_IDS": "67890,54321"}):
            provider = self.make_provider()
        claims = provider._verify_id_token(
            self.token({"id": 67890, "preferred_username": "invited_account"})
        )
        self.assertEqual(claims["id"], 67890)
        self.assertFalse((self.profile / "telegram-owner.json").exists())
        with self.assertRaises(ProviderError):
            provider._verify_id_token(self.token({"id": 99999, "preferred_username": "stranger"}))

    def test_removing_an_invitation_rejects_existing_tokens(self):
        with patch.dict(os.environ, {"HERMES_TELEGRAM_ALLOWED_IDS": "67890"}):
            provider = self.make_provider()
        token = self.token({"id": 67890, "preferred_username": "invited_account"})
        provider._claims_for(token)
        restarted = self.make_provider()
        with self.assertRaises(ProviderError):
            restarted._claims_for(token)
        self.assertEqual(restarted._claims_for(self.token())["id"], 12345)

    def test_invited_ids_do_not_replace_required_owner_configuration(self):
        with patch.dict(
            os.environ,
            {"HERMES_TELEGRAM_OWNER_USERNAME": "", "HERMES_TELEGRAM_ALLOWED_IDS": "67890"},
        ):
            provider = self.make_provider()
        self.assertFalse(provider.ready)
