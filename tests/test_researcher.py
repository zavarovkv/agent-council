import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "provision_researcher", ROOT / "scripts/provision-researcher.py"
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class ResearcherTests(unittest.TestCase):
    def test_route_merge_preserves_owner_credentials_and_other_routes(self):
        original = {
            "dashboard": {"auth": "owner", "secret": "test-secret"},
            "plugins": {"enabled": ["existing"], "disabled": ["basic"]},
            "delegation": {"backends": {"profile_routes": {"profiles": [{"name": "coder"}]}}},
        }
        updated = installer.configure_coordinator(original)
        self.assertEqual(updated["dashboard"], original["dashboard"])
        self.assertEqual(updated["plugins"]["enabled"], ["existing", installer.PLUGIN])
        self.assertEqual(updated["plugins"]["disabled"], ["basic"])
        routes = updated["delegation"]["backends"]["profile_routes"]
        self.assertEqual([p["name"] for p in routes["profiles"]], ["coder", "dexter"])
        self.assertNotIn("default", routes)
        self.assertEqual(installer.configure_coordinator(updated), updated)
        self.assertEqual(original["plugins"]["enabled"], ["existing"])

    def test_conflicting_policy_is_refused(self):
        for config in (
            {"plugins": {"disabled": [installer.PLUGIN]}},
            {"delegation": {"backend": "model_map"}},
            {
                "delegation": {
                    "backends": {
                        "profile_routes": {"profiles": [{"name": "dexter"}, {"name": "dexter"}]}
                    }
                }
            },
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                installer.configure_coordinator(config)

    def test_idempotent_install_keeps_reports_and_never_clones_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root_config = home / "config.yaml"
            root_config.write_text(
                json.dumps(
                    {
                        "model": {
                            "provider": "openai-codex",
                            "default": "test-model",
                            "api_key": "do-not-copy",
                        },
                        "agent": {"reasoning_effort": "medium"},
                        "telegram": {"token": "do-not-copy"},
                    }
                )
            )
            (home / ".env").write_text("TELEGRAM_BOT_TOKEN=do-not-copy\n")
            (home / "SOUL.md").write_text("private personal identity")
            created = []

            def create(name, **options):
                created.append(options)
                profile = home / "profiles" / name
                profile.mkdir(parents=True)
                (profile / "config.yaml").write_text("{}")
                return profile

            def read(path):
                return json.loads(path.read_text())

            def write(path, content):
                installer.atomic_private_write(path, json.dumps(content).encode())

            def install():
                return installer.provision(home, ROOT / "instructions", read, write, create)

            profile = install()
            report = profile / "research" / "report.md"
            report.write_text("saved evidence")
            install()
            self.assertEqual(len(created), 1)
            self.assertTrue(created[0]["no_skills"])
            self.assertNotIn("clone_from", created[0])
            self.assertFalse((profile / ".env").exists())
            child = read(profile / "config.yaml")
            self.assertNotIn("telegram", child)
            self.assertNotIn("api_key", child["model"])
            self.assertEqual(report.read_text(), "saved evidence")
            self.assertEqual((home / "SOUL.md").read_text(), "private personal identity")
            self.assertEqual((home / "config.yaml.before-dexter").stat().st_mode & 0o777, 0o600)
            (profile / "SOUL.md").write_text("user changes")
            with self.assertRaisesRegex(ValueError, "edited"):
                install()
            self.assertEqual(report.read_text(), "saved evidence")

    def test_unmanaged_profile_and_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "config.yaml").write_text("{}")
            profile = home / "profiles" / "dexter"
            profile.mkdir(parents=True)
            coordinator = {"model": {"provider": "openai-codex", "default": "test"}}
            with self.assertRaisesRegex(ValueError, "not managed"):
                installer.provision(home, ROOT / "instructions", lambda p: coordinator, None, None)
            profile.rmdir()
            profile.symlink_to(home, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                installer.provision(home, ROOT / "instructions", lambda p: coordinator, None, None)


if __name__ == "__main__":
    unittest.main()
