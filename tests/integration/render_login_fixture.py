"""Print the real image's HTML with a fake provider, without account credentials."""

from types import SimpleNamespace
from unittest.mock import patch

from hermes_cli.dashboard_auth.login_page import render_login_html

with patch(
    "hermes_cli.dashboard_auth.login_page.list_session_providers",
    return_value=[SimpleNamespace(name="telegram-owner", display_name="Telegram")],
):
    print(render_login_html(next_path="/sessions?tab=recent"))
