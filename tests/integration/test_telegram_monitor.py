"""Exercise the real pinned reader SDK offline."""

import subprocess
import unittest


class TelegramMonitorRuntimeTests(unittest.TestCase):
    def test_real_sdk_login_error_reports_stage_and_closes_without_leaking_secrets(self):
        result = subprocess.run(
            [
                "/opt/telegram-reader/bin/python",
                "-c",
                """
import asyncio, contextlib, io, os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telethon.errors import PhoneCodeExpiredError
from telethon.tl.functions.auth import ResendCodeRequest
from telethon.tl.types import auth
from telegram_monitor import setup

client = AsyncMock()
client.send_code_request.return_value = auth.SentCode(
    auth.SentCodeTypeApp(5), 'private-hash', next_type=auth.CodeTypeSms(), timeout=0
)
client.return_value = auth.SentCode(auth.SentCodeTypeSms(5), 'new-private-hash')
client.sign_in.side_effect = PhoneCodeExpiredError(request='private-request')
output = io.StringIO()
with patch.dict(os.environ, {'COUNCIL_TG_READER_SOURCE': 'fixture_source'}), \
     patch('telethon.TelegramClient', return_value=client), \
     patch('getpass.getpass', side_effect=['123', 'a' * 32, '+10000000000', 'resend', '54321']), \
     contextlib.redirect_stdout(output):
    assert asyncio.run(setup.main()) == 1
client.disconnect.assert_awaited_once()
client.send_code_request.assert_awaited_once()
client.assert_awaited_once()
assert isinstance(client.await_args.args[0], ResendCodeRequest)
assert client.await_args.args[0].phone_code_hash == 'private-hash'
client.sign_in.assert_awaited_once_with('+10000000000', code='54321', phone_code_hash='new-private-hash')
assert 'истек' in output.getvalue()
assert 'вход в аккаунт' in output.getvalue()
assert 'private' not in output.getvalue()
assert '+10000000000' not in output.getvalue()
assert '54321' not in output.getvalue()
print('login_failure_verified')
""",
            ],
            cwd="/opt/agent-council",
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        self.assertEqual(result.stdout.strip(), "login_failure_verified")

    def test_reader_dependencies_are_available_only_in_the_dedicated_interpreter(self):
        result = subprocess.run(
            [
                "/opt/telegram-reader/bin/python",
                "-c",
                "import telethon; from telethon.sessions import StringSession; assert telethon.__version__=='1.45.0'; assert StringSession().save()==''; print('sdk_ready')",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        self.assertEqual(result.stdout.strip(), "sdk_ready")
        import importlib.util

        self.assertIsNone(importlib.util.find_spec("telethon"))
