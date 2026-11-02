"""QR authorization remains bounded, private and cancelable."""

import asyncio
import io
import sys
import unittest
from contextlib import contextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_telegram_login import ERRORS

from telegram_monitor.login import SetupError
from telegram_monitor.qr import qr_screen, render_matrix, sign_in_qr


class QRTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        errors = patch.dict(sys.modules, {"telethon.errors": ERRORS})
        errors.start()
        self.addCleanup(errors.stop)
        terminal = patch("telegram_monitor.qr.require_terminal")
        terminal.start()
        self.addCleanup(terminal.stop)
        self.events = []
        self.client = AsyncMock()
        self.qr = SimpleNamespace(
            wait=AsyncMock(), recreate=AsyncMock(), url="tg://login?token=private"
        )
        self.client.qr_login.return_value = self.qr

        @contextmanager
        def screen(url):
            self.assertEqual(url, self.qr.url)
            self.events.append("show")
            try:
                yield
            finally:
                self.events.append("clear")

        screen_patch = patch("telegram_monitor.qr.qr_screen", side_effect=screen)
        screen_patch.start()
        self.addCleanup(screen_patch.stop)

    async def test_listener_starts_before_display_and_no_code_is_requested(self):
        async def wait():
            self.events.append("listen")
            await asyncio.sleep(0)

        self.qr.wait.side_effect = wait
        await sign_in_qr(self.client)
        self.assertEqual(self.events, ["listen", "show", "clear"])
        self.client.send_code_request.assert_not_called()
        self.qr.recreate.assert_not_called()

    async def test_expired_qr_is_cleared_then_renewed_with_bounded_attempts(self):
        self.qr.wait.side_effect = TimeoutError
        with self.assertRaisesRegex(SetupError, "qr_expired"):
            await sign_in_qr(self.client)
        self.assertEqual(self.qr.wait.await_count, 5)
        self.assertEqual(self.qr.recreate.await_count, 4)
        self.assertEqual(self.events, ["show", "clear"] * 5)

    async def test_password_prompt_happens_after_clearing_qr(self):
        self.qr.wait.side_effect = ERRORS.SessionPasswordNeededError

        async def password(client):
            self.assertEqual(self.events, ["show", "clear"])

        with patch("telegram_monitor.qr.sign_in_password", side_effect=password) as prompt:
            await sign_in_qr(self.client)
        prompt.assert_awaited_once_with(self.client)
        self.qr.recreate.assert_not_called()

    async def test_cancel_cleans_up_listener_and_screen_without_renewing(self):
        cleaned = asyncio.Event()

        async def wait():
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        self.qr.wait.side_effect = wait
        task = asyncio.create_task(sign_in_qr(self.client))
        for _ in range(3):
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned.is_set())
        self.assertEqual(self.events, ["show", "clear"])
        self.qr.recreate.assert_not_called()


class RenderTests(unittest.TestCase):
    def test_half_blocks_preserve_modules_and_odd_last_row(self):
        self.assertEqual(
            render_matrix([[0, 1, 0, 1], [0, 0, 1, 1], [1, 0, 1, 0]]), [" ▀▄█", "▀ ▀ "]
        )

    def test_non_terminal_never_outputs_bearer_token(self):
        fake = SimpleNamespace()
        with patch.dict(sys.modules, {"qrcode": fake}), redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(SetupError, "qr_terminal"), qr_screen("private"):
                pass
        self.assertEqual(output.getvalue(), "")

    def test_screen_is_restored_when_wait_raises(self):
        fake = SimpleNamespace(
            QRCode=lambda **kwargs: SimpleNamespace(
                add_data=lambda url: None,
                make=lambda **kw: None,
                get_matrix=lambda: [[False] * 9] * 9,
            ),
            constants=SimpleNamespace(ERROR_CORRECT_M=0),
        )
        with (
            patch.dict(sys.modules, {"qrcode": fake}),
            patch("telegram_monitor.qr.require_terminal"),
            patch("shutil.get_terminal_size", return_value=(80, 40)),
            redirect_stdout(io.StringIO()) as output,
        ):
            with self.assertRaises(ValueError), qr_screen("private"):
                raise ValueError("fixture")
        self.assertNotIn("private", output.getvalue())
        self.assertTrue(output.getvalue().endswith("\033[0m\033[2J\033[?25h\033[?1049l"))
