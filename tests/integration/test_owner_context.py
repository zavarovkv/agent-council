"""Pinned Hermes policies, prompt renderer and transactional migration, offline."""

import json
import os
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gateway.session_policy import build_policy, restore_policy
from gateway.session_policy_refresh import refreshed_policy
from hermes_state import SessionDB
from hermes_state_keys import local_identity
from hermes_state_local import commit_local_session, local_receipt
from hermes_state_mutation_prepared import commit_refreshed_policy
from hermes_state_runtime import RuntimeStoreError, admit_session_input, begin_runtime_epoch

from council_owner.context import AUDIENCE, SOURCE, audience_prompt, conversation
from council_owner.migration import adopt_guest_policy


class OwnerContextSDKTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        env = patch.dict(os.environ, HERMES_HOME=str(self.home), TERMINAL_CWD=str(self.home))
        env.start()
        self.addCleanup(env.stop)
        self.config = dict(
            model=dict(default="fixture-sol", provider="openai-codex"),
            agent=dict(reasoning_effort="high"),
            platform_toolsets=dict(cli=["file"], telegram=["memory", "skills"]),
        )
        self.db = SessionDB(self.home / "state.db")
        self.addCleanup(self.db.close)
        self.authority = SimpleNamespace(db=self.db, profile_id=str(self.home))
        self.epoch = begin_runtime_epoch(self.db, instance_id="fixture")

    def policy(self, source=SOURCE):
        params = dict(source=source)
        if source == SOURCE:
            params["follow_profile_config"] = True
        from gateway.run import _resolve_gateway_model

        return replace(
            build_policy(params, self.config, profile_terminal=False),
            model=_resolve_gateway_model(self.config),
        )

    def commit(self, policy):
        sid = local_identity(str(self.home), "fixture-owner", "fixture-create")
        receipt = dict(
            profile_id=str(self.home),
            principal_id="fixture-owner",
            request_id="fixture-create",
            session_id=sid,
            route="fixture-route",
            policy=asdict(policy),
            entry=dict(
                session_id=sid, created_at=datetime.now().isoformat(), origin=dict(chat_id=sid)
            ),
        )
        commit_local_session(self.db, epoch=self.epoch, receipt=receipt)
        return sid

    def test_telegram_surface_uses_profile_tools_and_follows_model_and_reasoning(self):
        policy = self.policy()
        self.assertEqual(restore_policy(json.loads(json.dumps(asdict(policy)))), policy)
        self.assertEqual(policy.platform, "telegram")
        self.assertEqual(set(policy.toolsets), {"memory", "skills"})
        self.assertNotEqual(policy.toolsets, self.policy("oneshot").toolsets)
        changed = {
            **self.config,
            "model": dict(default="fixture-next", provider="openai-codex"),
            "agent": dict(reasoning_effort="medium"),
            "platform_toolsets": dict(telegram=["file"]),
        }
        with patch("gateway.session_policy.profile_config", return_value=changed):
            effective, model = policy.effective(self.authority, secrets=False)
        self.assertEqual(model, "fixture-next")
        self.assertEqual(effective["agent"]["reasoning_effort"], "medium")
        self.assertEqual(policy.current_toolsets(effective), ("file",))
        self.assertEqual(policy.current_toolsets(self.config), ("memory", "skills"))

    def test_dialog_route_keeps_skill_tools_and_can_wait_for_clarification(self):
        from agent.oneshot_footprint import prune_oneshot_tools
        from gateway.config import Platform
        from gateway.run_turn_runner import TurnRunner
        from gateway.session_finite import finite_turn_scope

        tools = [{"function": {"name": "skill_manage"}}]
        with patch("gateway.session_policy.active_policy", return_value=self.policy()):
            self.assertEqual(prune_oneshot_tools(tools), tools)
        turn = SimpleNamespace(
            _ctx=SimpleNamespace(source=SimpleNamespace(platform=Platform.LOCAL)),
            _ask_clarify_question=lambda *args, **kwargs: ("Owner answer", True),
        )
        questions = [dict(qid="q", question="Which project?", choices=[], multi_select=False)]
        with (
            finite_turn_scope(False),
            patch("gateway.session_cli_turn.clarify_batch_turn", return_value=False),
        ):
            result = TurnRunner._clarify_callback_sync(turn, questions)
        self.assertEqual(result, {"answers": {"q": "Owner answer"}, "outcome": "submitted"})
        with finite_turn_scope(True):
            self.assertEqual(
                TurnRunner._clarify_callback_sync(turn, questions)["outcome"], "undelivered"
            )

    def legacy(self):
        policy = self.policy("oneshot")
        sid = self.commit(policy)
        job = dict(actor=12345, chat=-100, thread=7)
        title = conversation(job)
        self.db._execute_write(
            lambda conn: conn.execute("UPDATE sessions SET title=? WHERE id=?", (title, sid))
        )
        # Only the admission proof is replaced here; its private files/epoch/ID checks
        # have independent tests. All policy/receipt transactions are the native ones.
        with patch("council_owner.migration.admitted_title", return_value=True):
            adopted = adopt_guest_policy(self.authority, sid, policy, set())
        self.assertEqual(adopted.source, SOURCE)
        self.assertTrue(adopted.follows_profile)
        self.assertEqual(adopted.request_json, policy.request_json)
        refreshed, _, _ = refreshed_policy(adopted, self.config, json.loads(adopted.terminal_json))
        self.assertEqual(set(refreshed.toolsets), {"memory", "skills"})
        return sid, policy, refreshed

    def test_idle_migration_preserves_transcript_and_idempotency_clears_only_old_prompt(self):
        sid, old, new = self.legacy()
        self.db.append_message(sid, "user", "OLD_TRANSCRIPT_CANARY")
        self.db._execute_write(
            lambda conn: conn.execute(
                "UPDATE sessions SET system_prompt='old CLI prompt' WHERE id=?", (sid,)
            )
        )
        history = self.db.get_messages(sid)
        generation = commit_refreshed_policy(
            self.db,
            epoch=self.epoch,
            session_id=sid,
            old=json.loads(json.dumps(asdict(old))),
            new=asdict(new),
            generation=0,
        )
        self.assertEqual(generation, 1)
        self.assertEqual(self.db.get_messages(sid), history)
        self.assertIsNone(self.db.get_session(sid)["system_prompt"])
        self.assertEqual(restore_policy(local_receipt(self.db, sid)["policy"]), new)
        self.assertEqual(local_receipt(self.db, sid)["request_id"], "fixture-create")
        with self.assertRaises(RuntimeStoreError):
            commit_refreshed_policy(
                self.db,
                epoch=self.epoch,
                session_id=sid,
                old=json.loads(json.dumps(asdict(old))),
                new=asdict(new),
                generation=0,
            )

    def test_busy_session_is_not_migrated_or_prompt_invalidated(self):
        sid, old, new = self.legacy()
        self.db._execute_write(
            lambda conn: conn.execute(
                "UPDATE sessions SET system_prompt='preserve' WHERE id=?", (sid,)
            )
        )
        admit_session_input(
            self.db,
            epoch=self.epoch,
            principal_id="fixture-owner",
            session_id=sid,
            request_id="active",
            payload={"text": "fixture"},
        )
        with self.assertRaises(RuntimeStoreError):
            commit_refreshed_policy(
                self.db,
                epoch=self.epoch,
                session_id=sid,
                old=json.loads(json.dumps(asdict(old))),
                new=asdict(new),
                generation=0,
            )
        self.assertEqual(self.db.get_session(sid)["system_prompt"], "preserve")
        self.assertEqual(local_receipt(self.db, sid)["policy"], json.loads(json.dumps(asdict(old))))

    def test_unrelated_sessions_or_explicit_picks_are_not_adopted(self):
        old = self.policy("oneshot")
        sid = self.commit(old)
        self.assertIs(adopt_guest_policy(self.authority, sid, old, set()), old)
        with patch("council_owner.migration.admitted_title", return_value=True):
            self.assertIs(adopt_guest_policy(self.authority, sid, old, {"model"}), old)
            pinned = replace(old, request_json=json.dumps(dict(source="oneshot", model="explicit")))
            self.assertIs(adopt_guest_policy(self.authority, sid, pinned, set()), pinned)

    def test_real_renderer_shares_soul_skills_memory_and_telegram_hint(self):
        from agent.system_prompt import build_system_prompt_parts

        (self.home / "SOUL.md").write_text(
            "QUI_GON_PERSONA_CANARY. Отвечай кратко; подробнее по запросу."
        )
        skill = self.home / "skills/team/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: team\ndescription: TEAM_SKILL_CANARY\n---\nTeam instructions")

        def agent(platform):
            return SimpleNamespace(
                platform=platform,
                model="fixture-sol",
                provider="openai-codex",
                session_id="fixture",
                pass_session_id=False,
                _session_db=self.db,
                load_soul_identity=True,
                skip_context_files=True,
                valid_tool_names={"skills_list", "skill_view"},
                _tool_use_enforcement=False,
                _memory_store=SimpleNamespace(
                    format_for_system_prompt=lambda _: "PRIVATE_MEMORY_CANARY"
                ),
                _memory_enabled=True,
                _user_profile_enabled=True,
                _memory_manager=None,
                _environment_probe=False,
                _bot_mode_protocol=False,
            )

        dm = build_system_prompt_parts(agent("telegram"))
        guest = build_system_prompt_parts(agent(self.policy().platform))
        self.assertEqual(dm, guest)
        full = "\n".join(guest.values())
        for marker in ("QUI_GON_PERSONA_CANARY", "TEAM_SKILL_CANARY", "PRIVATE_MEMORY_CANARY"):
            self.assertIn(marker, full)
        self.assertNotIn("plain terminal (CLI)", full)
        self.assertNotIn(AUDIENCE, full)
        self.assertIn(AUDIENCE, audience_prompt(self.policy(), ""))
        # A rebuilt session sees the edited canonical instructions, without a guest copy.
        (self.home / "SOUL.md").write_text("UPDATED_PERSONA_CANARY")
        updated = build_system_prompt_parts(agent(self.policy().platform))
        self.assertIn("UPDATED_PERSONA_CANARY", updated["stable"])
