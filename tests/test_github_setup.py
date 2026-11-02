import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure = load_script("configure-github")
setup = load_script("setup-github")


class GitHubSetupTests(unittest.TestCase):
    def test_mcp_sdk_versions_and_non_success_responses(self):
        self.assertTrue(
            configure.successful_read(SimpleNamespace(is_error=False, content=["file"]))
        )
        self.assertTrue(configure.successful_read(SimpleNamespace(isError=False, content=["file"])))
        for response in (
            SimpleNamespace(is_error=True, content=["denied"]),
            SimpleNamespace(content=["input required"]),
            SimpleNamespace(is_error=False, content=[]),
        ):
            self.assertFalse(configure.successful_read(response))

    def test_other_account_is_rejected_before_repository_reads(self):
        with patch.object(configure, "github_get", return_value={"id": 99}) as request:
            with self.assertRaises(configure.SetupError):
                configure.verify_identity("fixture-secret", 4748829, ["zavarovkv/agent-council"])
            request.assert_called_once_with("/user", "fixture-secret")

    def test_existing_connection_cannot_be_replaced_by_another_endpoint(self):
        configure.validate_existing(None)
        configure.validate_existing(configure.server_config(enabled=True))
        with self.assertRaises(configure.SetupError):
            configure.validate_existing({"url": "https://outside.invalid/mcp"})

    def test_token_is_not_embedded_in_command_or_configuration(self):
        command = setup.command("authorize", ["zavarovkv/agent-council"])
        self.assertEqual(command[:2], ["ssh", "-T"])
        self.assertNotIn("fixture-secret", " ".join(command))
        self.assertEqual(
            configure.server_config(enabled=True)["headers"]["Authorization"],
            "Bearer ${MCP_GITHUB_API_KEY}",
        )
        for token in ("", "ghp_classic_token", "github_pat_" + "a" * 30 + "\ninjected"):
            with self.subTest(token_type=token[:10]), self.assertRaises(configure.SetupError):
                configure.normalize_token(token)

    def test_ssh_option_injection_is_rejected(self):
        with patch.dict("os.environ", {"COUNCIL_SSH_HOST": "-oProxyCommand=untrusted"}):
            with self.assertRaises(ValueError):
                setup.command("prepare", ["zavarovkv/agent-council"])


if __name__ == "__main__":
    unittest.main()
