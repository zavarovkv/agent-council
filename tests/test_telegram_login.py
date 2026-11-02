"""Login retries do not resend codes or expose credentials in diagnostics."""

import io
import sys
import unittest
from contextlib import redirect_stdout
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_monitor.login import SetupError, failure_message, sign_in

ERRORS = ModuleType("telethon.errors")
for name in (
    "PasswordHashInvalidError",
    "PhoneCodeEmptyError",
    "PhoneCodeInvalidError",
    "PhoneCodeExpiredError",
    "SessionPasswordNeededError",
    "FloodWaitError",
):
    setattr(ERRORS, name, type(name, (Exception,), {}))


class LoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        errors = patch.dict(sys.modules, {"telethon.errors": ERRORS})
        errors.start()
        self.addCleanup(errors.stop)
        self.client = AsyncMock()
        self.output = io.StringIO()
        output = redirect_stdout(self.output)
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    async def test_invalid_code_can_be_corrected_without_requesting_another_code(self):
        self.client.sign_in.side_effect = [ERRORS.PhoneCodeInvalidError("private"), None]
        with patch("getpass.getpass", side_effect=["11111", " 22 222 "]):
            await sign_in(self.client, "+10000000000", "private-code-hash")
        self.assertEqual(self.client.sign_in.await_count, 2)
        self.client.sign_in.assert_awaited_with(
            "+10000000000", code="22222", phone_code_hash="private-code-hash"
        )
        self.client.send_code_request.assert_not_called()
        self.assertNotIn("private", self.output.getvalue())

    async def test_retry_limit_does_not_fall_through_to_password(self):
        self.client.sign_in.side_effect = ERRORS.PhoneCodeInvalidError("secret")
        with patch("getpass.getpass", return_value="11111") as prompt:
            with self.assertRaisesRegex(SetupError, "code_attempts"):
                await sign_in(self.client, "+10000000000", "hash")
        self.assertEqual(prompt.call_count, 3)
        self.assertEqual(self.client.sign_in.await_count, 3)

    async def test_malformed_code_is_not_sent_to_telegram(self):
        with patch("getpass.getpass", side_effect=["", "not a code", "12345"]):
            await sign_in(self.client, "+10000000000", "hash")
        self.assertEqual(self.client.sign_in.await_count, 1)

    async def test_provider_word_phrase_and_call_codes_keep_the_expected_format(self):
        for kind, entered, expected in (
            ("SentCodeTypeSmsWord", " green ", "green"),
            ("SentCodeTypeSmsPhrase", " green tea ", "green tea"),
            ("SentCodeTypeFlashCall", "+1 234 567", "+1234567"),
        ):
            self.client.reset_mock()
            prompt = SimpleNamespace(kind=kind, read=AsyncMock(return_value=(entered, "new-hash")))
            await sign_in(self.client, "+10000000000", "old-hash", code_prompt=prompt)
            self.client.sign_in.assert_awaited_once_with(
                "+10000000000", code=expected, phone_code_hash="new-hash"
            )

    async def test_expiration_and_flood_wait_stop_without_resending(self):
        for error in (ERRORS.PhoneCodeExpiredError, ERRORS.FloodWaitError):
            self.client.reset_mock()
            self.client.sign_in.side_effect = error("private")
            with patch("getpass.getpass", return_value="12345") as prompt:
                with self.assertRaises(error):
                    await sign_in(self.client, "+10000000000", "hash")
            self.assertEqual(prompt.call_count, 1)
            self.client.send_code_request.assert_not_called()

    async def test_two_step_password_can_be_retried_without_repeating_the_code(self):
        self.client.sign_in.side_effect = [
            ERRORS.SessionPasswordNeededError(),
            ERRORS.PasswordHashInvalidError("private"),
            None,
        ]
        with patch("getpass.getpass", side_effect=["12345", "wrong", "correct"]):
            await sign_in(self.client, "+10000000000", "hash")
        self.client.sign_in.assert_awaited_with(password="correct")
        self.assertEqual(self.client.sign_in.await_count, 3)
        self.assertNotIn("private", self.output.getvalue())

    async def test_password_attempts_are_bounded(self):
        self.client.sign_in.side_effect = [
            ERRORS.SessionPasswordNeededError(),
            *[ERRORS.PasswordHashInvalidError() for _ in range(3)],
        ]
        with patch("getpass.getpass", side_effect=["12345", "wrong", "wrong", "wrong"]):
            with self.assertRaisesRegex(SetupError, "password_attempts"):
                await sign_in(self.client, "+10000000000", "hash")
        self.assertEqual(self.client.sign_in.await_count, 4)


class DiagnosticTests(unittest.TestCase):
    def test_only_fixed_messages_and_stages_are_disclosed(self):
        for error in (
            ValueError("private phone hash code password"),
            SetupError("private phone hash code password"),
            ERRORS.PhoneCodeExpiredError("private phone hash code password"),
            KeyboardInterrupt("private phone hash code password"),
        ):
            result = failure_message(error, "sign_in")
            self.assertNotIn("private", result)
            self.assertIn("вход в аккаунт", result)
        self.assertIn("истек", failure_message(ERRORS.PhoneCodeExpiredError(), "sign_in"))

    def test_flood_wait_only_prints_a_bounded_integer(self):
        error = ERRORS.FloodWaitError("private")
        error.seconds = 120
        self.assertIn("120 сек", failure_message(error, "send_code"))
        error.seconds = "private"
        self.assertNotIn("private", failure_message(error, "send_code"))
