"""Verify native clarification identities and ContextVar-bound delegation offline."""

import importlib.util
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gateway.platforms.api_server import APIServerAdapter
from gateway.platforms.api_server_authority_runs import respond_run
from hermes_state_runtime import RuntimeStoreError


class NativeLinearControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_control_requires_exact_prompt_and_execution_generation(self):
        authority = SimpleNamespace(
            profile_id="fixture",
            attach=AsyncMock(
                return_value=SimpleNamespace(
                    prompts=[{"prompt_id": "prompt-a", "kind": "clarify"}],
                    subscription_id="fixture-subscription",
                )
            ),
            detach=AsyncMock(),
            respond=AsyncMock(return_value={"answered": True}),
        )
        row = {"status": "started", "generation": 3, "target_session_id": "fixture-session"}
        body = {"request_id": "prompt-a", "execution_generation": 3, "answer": "Owner answer"}
        with patch(
            "gateway.platforms.api_server_authority_runs.run_admission",
            return_value=(authority, row),
        ):
            self.assertEqual(
                await respond_run(None, "run_fixture", body, kind="clarify"), {"answered": True}
            )
            self.assertEqual(authority.respond.call_args.args[2:4], (3, "prompt-a"))
            for invalid in (
                body | {"execution_generation": 2},
                body | {"request_id": "stale-prompt"},
                body | {"extra": True},
            ):
                with self.assertRaises(RuntimeStoreError):
                    await respond_run(None, "run_fixture", invalid, kind="clarify")
        self.assertEqual(authority.respond.await_count, 1)

    async def test_native_api_context_overrides_model_supplied_parent_identity(self):
        spec = importlib.util.spec_from_file_location(
            "council_linear_fixture", "/opt/hermes/plugins/council-linear/__init__.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        captured = []

        class Opener:
            def open(self, request, timeout):
                captured.append(json.loads(request.data))
                return self

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _size):
                return b'{"issue_id":"fixture"}'

        # APIServerAdapter uses the real task-local context binder for native runs.
        tokens = APIServerAdapter._bind_api_server_session(
            session_key="run_real", session_id="real-session"
        )
        try:
            with (
                patch.dict(
                    os.environ, {"COUNCIL_LINEAR_AGENT": "qui-gon", "API_SERVER_KEY": "a" * 64}
                ),
                patch.object(module.urllib.request, "build_opener", return_value=Opener()),
            ):
                result = module.call(
                    "delegate",
                    {"run": "forged-run", "session": "forged-session", "target": "dexter"},
                )
            self.assertEqual(json.loads(result), {"issue_id": "fixture"})
            self.assertEqual(captured[0]["run"], "run_real")
            self.assertEqual(captured[0]["session"], "real-session")
        finally:
            for token in reversed(tokens):
                token.var.reset(token)
