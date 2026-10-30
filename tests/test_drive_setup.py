import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), ROOT / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure = load_script("configure-drive")
setup = load_script("setup-drive")


def client_fixture():
    return {
        "installed": {
            "client_id": "fake.apps.googleusercontent.com",
            "client_secret": "not-a-real-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


class DriveSetupTests(unittest.TestCase):
    def test_credential_import_is_private_and_preserves_existing_configuration(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            home = Path(directory)
            raw = json.dumps(client_fixture()).encode()
            configure.import_client(home, raw)
            path = home / "google_client_secret.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            configure.import_client(home, raw)
            changed = client_fixture()
            changed["installed"]["client_id"] = "different.apps.googleusercontent.com"
            with self.assertRaises(configure.SetupError):
                configure.import_client(home, json.dumps(changed).encode())
            self.assertEqual(json.loads(path.read_text()), client_fixture())
            self.assertNotIn("not-a-real-secret", output.getvalue())

    def test_non_google_endpoints_web_clients_and_oversized_inputs_are_refused(self):
        for field in ("auth_uri", "token_uri"):
            with self.subTest(field=field):
                data = client_fixture()
                data["installed"][field] = "https://outside.invalid/collect"
                with self.assertRaises(configure.SetupError):
                    configure.validate_client(data)
        with self.assertRaises(configure.SetupError):
            configure.validate_client({"web": client_fixture()["installed"]})
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(configure.SetupError):
            configure.import_client(Path(directory), b"x" * (configure.MAX_CLIENT_BYTES + 1))

    def test_symlink_is_not_overwritten_and_short_lived_grant_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            original = home / "original.json"
            original.write_text("untouched")
            target = home / "google_token.json"
            target.symlink_to(original)
            with self.assertRaises(configure.SetupError):
                configure.private_write(target, {"token": "fake"})
            self.assertEqual(original.read_text(), "untouched")
            target.unlink()
            with self.assertRaises(configure.SetupError):
                configure.save_credentials(home, SimpleNamespace(refresh_token=None))
            self.assertFalse(target.exists())

    def test_workspace_must_be_explicit_and_cannot_inject_a_drive_query(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / "council-workspace.json"
            for data in (
                {},
                {"files": {"provider": "google_drive", "folder_id": "x' or trashed=true"}},
            ):
                path.write_text(json.dumps(data))
                with self.assertRaises(configure.SetupError):
                    configure.folder_id(home)

    def test_local_client_bytes_use_stdin_and_never_ssh_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client.json"
            raw = json.dumps(client_fixture()).encode()
            path.write_bytes(raw)
            for mode, calls in (("--verify", 2), ("--import-only", 1)):
                with (
                    self.subTest(mode=mode),
                    patch("sys.argv", ["setup-drive.py", "--client-json", str(path), mode]),
                    patch.object(setup.subprocess, "run") as run,
                ):
                    setup.main()
                    self.assertEqual(run.call_count, calls)
                    self.assertEqual(run.call_args_list[0].kwargs["input"], raw)
                    for call in run.call_args_list:
                        self.assertNotIn("not-a-real-secret", " ".join(call.args[0]))
                        self.assertIn(
                            "flock -n /etc/agent-council/operations.lock", call.args[0][-1]
                        )

    def test_callback_selects_hermes_network_when_the_secretary_network_is_also_present(self):
        address = setup.callback_address(
            {
                "agent-council_secretary": {"IPAddress": "172.31.0.3"},
                "agent-council_hermes": {"IPAddress": "172.30.90.2"},
            }
        )
        self.assertEqual(str(address), "172.30.90.2")
        for networks in ({}, {"agent-council_hermes": {"IPAddress": "8.8.8.8"}}):
            with self.subTest(networks=networks), self.assertRaises(ValueError):
                setup.callback_address(networks)


if __name__ == "__main__":
    unittest.main()
