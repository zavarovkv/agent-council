"""The themed renderer must preserve upstream auth links and escaping."""

import unittest
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from hermes_cli.dashboard_auth.login_page import render_login_html


class Links(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.links = []
        self.tags = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "a":
            self.links.append(dict(attrs))


class LoginThemeTests(unittest.TestCase):
    def render(self, providers, **kwargs):
        with patch(
            "hermes_cli.dashboard_auth.login_page.list_session_providers", return_value=providers
        ):
            return render_login_html(**kwargs)

    def test_themed_page_preserves_provider_next_and_proxy_prefix(self):
        provider = SimpleNamespace(name="telegram-owner", display_name="Telegram")
        next_path = "/sessions?query=one&tab=two"
        document = self.render([provider], next_path=next_path, prefix="/council")
        link = Links(document).links[0]
        target = urlsplit(link["href"])
        self.assertEqual(target.path, "/council/auth/login")
        self.assertEqual(
            parse_qs(target.query), {"provider": ["telegram-owner"], "next": [next_path]}
        )
        self.assertIn('<main data-prefix="/council">', document)

    def test_provider_labels_remain_escaped(self):
        provider = SimpleNamespace(name='test" onclick="bad', display_name="<script>bad()</script>")
        document = self.render([provider], next_path='/" onmouseover="bad')
        parsed = Links(document)
        self.assertNotIn("script", parsed.tags)
        self.assertNotIn("onclick", parsed.links[0])
        self.assertEqual(
            parse_qs(urlsplit(parsed.links[0]["href"]).query)["provider"], [provider.name]
        )

    def test_unavailable_page_keeps_authentication_closed(self):
        document = self.render([])
        self.assertIn("Вход пока недоступен", document)
        self.assertEqual(Links(document).links, [])
        self.assertNotIn("form", Links(document).tags)
