import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_operations import FakeRuntime, Fixture

from council_ops.config import OperationError, read_settings
from council_ops.instructions import (
    FAILURE,
    MARKER,
    InstructionReleaseError,
    apply,
    checkout,
    collect,
    export,
    install_files,
    sync,
)
from council_ops.lifecycle import deploy
from secretary_worker.instructions import configured_instructions

REVISION = "d" * 40


class PrivateInstructionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = Fixture(temporary.name)
        self.home = self.fixture.data
        self.source = self.fixture.root / "private-source"
        (self.source / "assistant/skills/custom/scripts").mkdir(parents=True)
        self.source.chmod(0o700)
        (self.source / "assistant/SOUL.md").write_text("Private assistant identity")
        (self.source / "assistant/skills/custom/SKILL.md").write_text(
            "---\nname: custom\n---\nFixture skill"
        )
        script = self.source / "assistant/skills/custom/scripts/run.py"
        script.write_text("print('fixture')\n")
        script.chmod(0o700)
        self.runtime = FakeRuntime(self.fixture.settings())
        self.uid, self.gid = os.getuid(), os.getgid()
        self.identity = patch(
            "council_ops.instructions.identity", return_value=(self.uid, self.gid)
        )
        self.identity.start()
        self.addCleanup(self.identity.stop)
        lifecycle_identity = patch(
            "council_ops.lifecycle.identity", return_value=(self.uid, self.gid)
        )
        lifecycle_identity.start()
        self.addCleanup(lifecycle_identity.stop)

    def install(self):
        files = collect(self.source, self.home)
        install_files(self.home, files, REVISION, self.uid, self.gid)
        return files

    def test_install_adopts_identical_files_preserves_unrelated_state_and_script_mode(self):
        (self.home / "SOUL.md").write_text("Private assistant identity")
        self.install()
        self.assertEqual((self.home / "skills/custom/scripts/run.py").stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.home / "SOUL.md").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.home / "history.txt").read_text(), "original history")
        self.assertEqual(json.loads((self.home / MARKER).read_text())["revision"], REVISION)

    def test_combined_code_instruction_release_rolls_back_both_and_uses_one_cutover(self):
        self.install()
        (self.source / "assistant/SOUL.md").write_text("Reviewed next identity")

        def git(*arguments):
            return subprocess.run(
                ["git", "-C", str(self.source), *arguments],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        git("init", "--initial-branch=main")
        git("add", ".")
        git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-m",
            "Reviewed private release",
        )
        revision = git("rev-parse", "HEAD")
        original = self.runtime.run

        def run(arguments, **kwargs):
            if arguments[0] == "git":
                return subprocess.run(
                    arguments, check=True, capture_output=True, text=True
                ).stdout.strip()
            return original(arguments, **kwargs)

        self.runtime.fail_candidate = True
        with patch.object(self.runtime, "run", side_effect=run):
            with self.assertRaises(OperationError):
                deploy(self.runtime, instruction_source=self.source, instruction_revision=revision)
            self.assertEqual((self.home / "SOUL.md").read_text(), "Private assistant identity")
            self.assertEqual(json.loads((self.home / MARKER).read_text())["revision"], REVISION)
            self.runtime.fail_candidate = False
            self.runtime.events.clear()
            result = deploy(
                self.runtime, instruction_source=self.source, instruction_revision=revision
            )
        self.assertEqual(result["instructions_revision"], revision)
        self.assertEqual((self.home / "SOUL.md").read_text(), "Reviewed next identity")
        self.assertEqual(self.runtime.events.count("stop"), 1)

    def test_local_edits_are_preserved_and_can_be_adopted_only_as_the_reviewed_candidate(self):
        self.install()
        (self.home / "SOUL.md").write_text("Local edits")
        with self.assertRaises(OperationError):
            self.install()
        self.assertEqual((self.home / "SOUL.md").read_text(), "Local edits")
        (self.source / "assistant/SOUL.md").write_text("Local edits")
        self.install()
        self.assertEqual((self.home / "SOUL.md").read_text(), "Local edits")

    def test_unmanaged_different_file_symlink_and_credential_content_are_rejected(self):
        (self.home / "SOUL.md").write_text("Existing identity")
        with self.assertRaises(OperationError):
            self.install()
        (self.home / "SOUL.md").unlink()
        (self.home / "skills").symlink_to(self.fixture.workspace)
        with self.assertRaises(OperationError):
            self.install()
        (self.home / "skills").unlink()
        (self.source / "assistant/SOUL.md").write_text("ghp_" + "a" * 36)
        with self.assertRaises(OperationError):
            collect(self.source, self.home)
        (self.source / "assistant/SOUL.md").write_text("Identity")
        (self.source / "assistant/skills/custom/.env").write_text("placeholder")
        with self.assertRaises(OperationError):
            collect(self.source, self.home)

    def test_managed_removal_does_not_delete_unrelated_skill_files(self):
        self.install()
        extra = self.home / "skills/custom/user-note.md"
        extra.write_text("Unmanaged local note")
        shutil.rmtree(self.source / "assistant/skills")
        self.install()
        self.assertFalse((self.home / "skills/custom/SKILL.md").exists())
        self.assertEqual(extra.read_text(), "Unmanaged local note")

    def test_worker_profile_rules_have_no_fallback_when_missing_corrupted_or_symlinked(self):
        environment = {"COUNCIL_SECRETARY_INSTRUCTIONS": "profile"}
        with self.assertRaises(FileNotFoundError):
            configured_instructions(self.home, environment)
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "instructions/secretary",
            self.home / "instructions",
        )
        expected = configured_instructions(self.home, environment)
        self.assertIn("spouse", expected.skills)
        (self.home / "instructions/POLICY.md").write_text(" ")
        with self.assertRaises(ValueError):
            configured_instructions(self.home, environment)
        shutil.rmtree(self.home / "instructions")
        (self.home / "instructions").symlink_to(self.source)
        with self.assertRaises(ValueError):
            configured_instructions(self.home, environment)

    def test_export_stages_edits_and_new_skills_without_touching_runtime_or_committing(self):
        self.install()
        (self.home / "SOUL.md").write_text("Reviewed later")
        skill = self.home / "skills/new-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: new\n---\nNew skill")
        with patch("council_ops.instructions.checkout"):
            result = export(self.runtime, self.source, REVISION, include_skills=["new-skill"])
        self.assertTrue(result["commit_required"])
        self.assertFalse(result["service_restarted"])
        self.assertEqual((self.source / "assistant/SOUL.md").read_text(), "Reviewed later")
        self.assertTrue((self.source / "assistant/skills/new-skill/SKILL.md").is_file())
        self.assertEqual((self.home / "SOUL.md").read_text(), "Reviewed later")
        self.assertNotIn("stop", self.runtime.events)

    def test_apply_checks_a_copy_then_restores_state_and_credentials_after_failed_restart(self):
        (self.home / "SOUL.md").write_text("Private assistant identity")
        before = self.fixture.auth.read_bytes()
        with (
            patch("council_ops.instructions.checkout"),
            patch.object(
                self.runtime, "ready", side_effect=[OperationError("fixture failure"), None]
            ),
        ):
            with self.assertRaises(OperationError):
                apply(self.runtime, self.source, REVISION)
        self.assertEqual((self.home / "SOUL.md").read_text(), "Private assistant identity")
        self.assertFalse((self.home / MARKER).exists())
        self.assertEqual(self.fixture.auth.read_bytes(), before)
        self.assertTrue(self.runtime.running)

    def test_apply_keeps_literal_secretary_settings_and_skips_an_identical_revision(self):
        (self.home / ".council/secretary").mkdir(parents=True)
        (self.home / ".council/secretary/.council-secretary.json").write_text('{"schema":1}')
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "instructions/secretary",
            self.source / "secretary",
        )
        secret = self.runtime.settings.secretary_file
        secret.write_text(
            "COUNCIL_SECRETARY_KEY=" + "a" * 64 + "\nCOUNCIL_SECRETARY_OWNER_ID=12345\n"
        )
        with patch("council_ops.instructions.checkout"):
            apply(self.runtime, self.source, REVISION)
            failure = self.runtime.settings.env_file.with_name(FAILURE)
            failure.write_text(json.dumps({"revision": REVISION}))
            self.runtime.events.clear()
            result = apply(self.runtime, self.source, REVISION)
        self.assertFalse(failure.exists())
        values = read_settings(secret)
        self.assertEqual(values["COUNCIL_SECRETARY_KEY"], "a" * 64)
        self.assertEqual(values["COUNCIL_SECRETARY_OWNER_ID"], "12345")
        self.assertEqual(values["COUNCIL_SECRETARY_INSTRUCTIONS"], "profile")
        self.assertFalse(result["changed"])
        self.assertNotIn("stop", self.runtime.events)

    def test_incompatible_worker_is_rejected_before_any_downtime(self):
        (self.home / ".council/secretary").mkdir(parents=True)
        (self.home / ".council/secretary/.council-secretary.json").write_text('{"schema":1}')
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "instructions/secretary",
            self.source / "secretary",
        )
        original = self.runtime.run

        def older_image(arguments, **kwargs):
            if arguments[-1] == "from secretary_worker.instructions import configured_instructions":
                raise OperationError("Fixture: previous image has no private loader")
            return original(arguments, **kwargs)

        with (
            patch("council_ops.instructions.checkout"),
            patch.object(self.runtime, "run", side_effect=older_image),
        ):
            with self.assertRaisesRegex(OperationError, "compatible"):
                apply(self.runtime, self.source, REVISION)
        self.assertNotIn("stop", self.runtime.events)

    def test_failed_auto_release_is_not_retried_until_a_new_commit_or_explicit_retry(self):
        self.install()
        original = self.runtime.run

        def run(arguments, **kwargs):
            if arguments[0] == "git":
                return "f" * 40 if arguments[-1] == "FETCH_HEAD" else REVISION
            return original(arguments, **kwargs)

        with (
            patch("council_ops.instructions.clean_checkout", return_value=["git"]),
            patch.object(self.runtime, "run", side_effect=run),
            patch(
                "council_ops.instructions.apply",
                side_effect=InstructionReleaseError("Fixture validation failure"),
            ) as release,
        ):
            with self.assertRaises(InstructionReleaseError):
                sync(self.runtime, self.source)
            with self.assertRaisesRegex(OperationError, "failed release validation"):
                sync(self.runtime, self.source)
            self.assertEqual(release.call_count, 1)
        self.assertEqual(
            json.loads(self.runtime.settings.env_file.with_name(FAILURE).read_text())["revision"],
            "f" * 40,
        )

    def test_checkout_rejects_wrong_revision_dirty_ignored_files_and_nested_checkouts(self):
        def git(*arguments):
            return subprocess.run(
                ["git", "-C", str(self.source), *arguments],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        git("init", "--initial-branch=main")
        (self.source / ".gitignore").write_text("*.env\n")
        git("add", ".")
        git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-m",
            "Fixture",
        )
        revision = git("rev-parse", "HEAD")
        runtime = SimpleNamespace(
            settings=self.runtime.settings,
            run=lambda arguments: subprocess.run(
                arguments, check=True, capture_output=True, text=True
            ).stdout.strip(),
        )
        checkout(runtime, self.source, revision)
        self.source.chmod(0o755)
        with self.assertRaises(OperationError):
            checkout(runtime, self.source, revision)
        self.source.chmod(0o700)
        with self.assertRaises(OperationError):
            checkout(runtime, self.source, "0" * 40)
        (self.source / "secret.env").write_text("fixture")
        with self.assertRaises(OperationError):
            checkout(runtime, self.source, revision)
        (self.source / "secret.env").unlink()
        with self.assertRaises(OperationError):
            checkout(runtime, self.source / "assistant", revision)

    def test_automatic_main_update_noop_and_drift_preservation_use_real_git(self):
        def git(*arguments):
            return subprocess.run(
                ["git", "-C", str(self.source), *arguments],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        def commit():
            git("add", ".")
            git(
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "-m",
                "Fixture change",
            )
            return git("rev-parse", "HEAD")

        git("init", "--initial-branch=main")
        first = commit()
        remote = self.fixture.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        git("remote", "add", "origin", str(remote))
        git("push", "origin", "main")
        install_files(self.home, collect(self.source, self.home), first, self.uid, self.gid)
        (self.source / "assistant/SOUL.md").write_text("Updated from approved main")
        second = commit()
        git("push", "origin", "main")
        git("switch", "--detach", first)
        original = self.runtime.run

        def run(arguments, **kwargs):
            if arguments[0] == "git":
                return subprocess.run(
                    arguments, check=True, capture_output=True, text=True
                ).stdout.strip()
            return original(arguments, **kwargs)

        with patch.object(self.runtime, "run", side_effect=run):
            result = sync(self.runtime, self.source)
            self.assertEqual(result["instructions_revision"], second)
            self.assertEqual((self.home / "SOUL.md").read_text(), "Updated from approved main")
            git("switch", "main")
            (self.source / "README.md").write_text("Documentation only")
            third = commit()
            git("push", "origin", "main")
            git("switch", "--detach", second)
            self.runtime.events.clear()
            self.assertFalse(sync(self.runtime, self.source)["changed"])
            self.assertNotIn("stop", self.runtime.events)
            self.assertEqual(json.loads((self.home / MARKER).read_text())["revision"], third)
            git("switch", "main")
            (self.source / "assistant/SOUL.md").write_text("Next approved instruction")
            commit()
            git("push", "origin", "main")
            git("switch", "--detach", third)
            (self.home / "SOUL.md").write_text("Unsaved local edit")
            self.runtime.events.clear()
            with self.assertRaises(OperationError):
                sync(self.runtime, self.source)
            self.assertEqual(git("rev-parse", "HEAD"), third)
            self.assertEqual((self.home / "SOUL.md").read_text(), "Unsaved local edit")
            self.assertNotIn("stop", self.runtime.events)
