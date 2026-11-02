"""Explicit code resend honors provider cooldown, rotates hashes and hides receipts."""

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_monitor.codes import CodePrompt
from telegram_monitor.login import SetupError


def receipt(*, code_hash="private-hash", kind="SentCodeTypeApp", timeout=30, next_type=True):
    return SimpleNamespace(
        type=type(kind, (), {})(),
        phone_code_hash=code_hash,
        timeout=timeout,
        next_type=object() if next_type else None,
        email_pattern="private-email",
    )


class CodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.output = io.StringIO()
        capture = redirect_stdout(self.output)
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)
        self.now = 0
        self.client = AsyncMock()

    async def test_early_resend_does_not_send_and_later_resend_uses_new_hash(self):
        prompt = CodePrompt(receipt(), clock=lambda: self.now)

        def enter(_label):
            self.now += 20
            return "resend" if self.now < 60 else "12345"

        with (
            patch("getpass.getpass", side_effect=enter),
            patch(
                "telegram_monitor.codes.resend_code",
                new_callable=AsyncMock,
                return_value=receipt(code_hash="new-private-hash", next_type=False),
            ) as resend,
        ):
            self.assertEqual(
                await prompt.read(self.client, "private-phone"), ("12345", "new-private-hash")
            )
        resend.assert_awaited_once_with(self.client, "private-phone", "private-hash")
        self.assertIn("Подождите 10 сек", self.output.getvalue())
        self.assertNotIn("private", self.output.getvalue())

    async def test_entering_code_never_automatically_resends(self):
        prompt = CodePrompt(receipt(), clock=lambda: self.now)
        with (
            patch("getpass.getpass", return_value="12345"),
            patch("telegram_monitor.codes.resend_code", new_callable=AsyncMock) as resend,
        ):
            self.assertEqual(await prompt.read(self.client, "phone"), ("12345", "private-hash"))
        resend.assert_not_called()

    async def test_provider_without_next_method_cannot_be_forced_to_resend(self):
        prompt = CodePrompt(receipt(next_type=False), clock=lambda: self.now)
        with (
            patch("getpass.getpass", return_value="resend"),
            patch("telegram_monitor.codes.resend_code", new_callable=AsyncMock) as resend,
        ):
            with self.assertRaisesRegex(SetupError, "resend_unavailable"):
                await prompt.read(self.client, "phone")
        resend.assert_not_called()

    async def test_resends_are_bounded_and_server_errors_are_not_retried(self):
        prompt = CodePrompt(receipt(timeout=0), clock=lambda: self.now)
        with (
            patch("getpass.getpass", return_value="resend"),
            patch(
                "telegram_monitor.codes.resend_code",
                new_callable=AsyncMock,
                return_value=receipt(timeout=0),
            ) as resend,
        ):
            with self.assertRaisesRegex(SetupError, "resend_limit"):
                await prompt.read(self.client, "phone")
        self.assertEqual(resend.await_count, 2)
        prompt = CodePrompt(receipt(timeout=0), clock=lambda: self.now)
        with (
            patch("getpass.getpass", return_value="resend"),
            patch(
                "telegram_monitor.codes.resend_code",
                new_callable=AsyncMock,
                side_effect=RuntimeError("private"),
            ) as resend,
        ):
            with self.assertRaises(RuntimeError):
                await prompt.read(self.client, "phone")
        resend.assert_awaited_once()
        self.assertNotIn("private", self.output.getvalue())

    def test_delivery_is_explained_without_address_or_raw_provider_response(self):
        CodePrompt(receipt(kind="SentCodeTypeEmailCode"))
        self.assertIn("на почту", self.output.getvalue())
        self.assertNotIn("private", self.output.getvalue())
        with self.assertRaisesRegex(SetupError, "unsupported_code_delivery"):
            CodePrompt(receipt(kind="UnsupportedPrivateType"))
