"""Opt-in guest replies, with an image compatibility floor and immediate revocation."""

import json
import os
import re
import secrets
import urllib.request

from telegram_secretary.guest_policy import RELATIVE_POLICY, load_state
from telegram_secretary.policy import Settings as SecretarySettings

from .config import OperationError, atomic_write
from .instructions import verify_capabilities
from .lifecycle import identity, smoke
from .monitoring import literal_value
from .snapshots import snapshot

CAPABILITY = "telegram-guests-v1"
OWNER_CAPABILITY = "telegram-guest-owner-v1"
AGENT_CAPABILITY = "telegram-guest-agent-v1"
CONTEXT_CAPABILITY = "telegram-guest-context-v1"


def require_compatible(runtime, *homes, image=None):
    # Even a disabled policy keeps the guard mandatory: Telegram can still deliver
    # guest updates, and older effective_message handlers may treat them as private.
    if any(
        (home / RELATIVE_POLICY).exists() or (home / RELATIVE_POLICY).is_symlink() for home in homes
    ):
        required = [CAPABILITY]
        if any(
            ((home / RELATIVE_POLICY).exists() or (home / RELATIVE_POLICY).is_symlink())
            and load_state(home).schema >= 2
            for home in homes
        ):
            required.append(OWNER_CAPABILITY)
        if any(load_state(home).schema >= 3 for home in homes):
            required.append(AGENT_CAPABILITY)
        if any(load_state(home).schema >= 4 for home in homes):
            required.append(CONTEXT_CAPABILITY)
        verify_capabilities(runtime, required, image=image)


def platform_enabled(settings):
    token = literal_value(
        settings.data_dir / ".env", "TELEGRAM_BOT_TOKEN", owner=int(settings.values["HERMES_UID"])
    )
    if not re.fullmatch(r"[0-9]{5,}:[A-Za-z0-9_-]{30,128}", token):
        raise OperationError("Invalid Telegram credential.")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open("https://api.telegram.org/bot" + token + "/getMe", timeout=10) as response:
            result = json.loads(response.read(65536))
        if result.get("ok") is not True or not isinstance(result.get("result"), dict):
            raise ValueError("Invalid response")
        return result["result"].get("supports_guest_queries") is True
    except Exception:
        raise OperationError(
            "Telegram readiness unavailable; private transport details omitted."
        ) from None


def configure(runtime, *, enabled=None, owner_agent=None):
    settings = runtime.settings
    state = load_state(settings.data_dir)
    if enabled is None and owner_agent is None:
        return {
            "enabled": state.enabled,
            "policy_valid": not state.error,
            "restart_required": False,
            "owner_only": True,
            "owner_agent": state.owner_agent,
        }
    enabled = state.enabled if enabled is None else enabled
    owner_agent = state.owner_agent if owner_agent is None else owner_agent
    schema = (
        4 if (enabled and owner_agent) or state.schema == 4 else (3 if state.schema == 3 else 2)
    )
    if enabled:
        verify_capabilities(
            runtime,
            [CAPABILITY, OWNER_CAPABILITY]
            + ([AGENT_CAPABILITY] if schema >= 3 else [])
            + ([CONTEXT_CAPABILITY] if schema >= 4 else []),
        )
        inspected = runtime.inspect()
        environment = dict(item.split("=", 1) for item in inspected["Config"]["Env"] if "=" in item)
        policy = SecretarySettings.load({**environment, "HERMES_HOME": str(settings.data_dir)})
        companion = runtime.companion()
        if not policy.enabled or not companion:
            raise OperationError("Prepare the isolated secretary and pinned owner first.")
        worker = json.loads(runtime.run(["docker", "inspect", companion]))[0]
        if (
            worker["Image"] != inspected["Image"]
            or worker["State"].get("Health", {}).get("Status") != "healthy"
        ):
            raise OperationError(
                "The matching isolated worker must be healthy before guest replies."
            )
        if not platform_enabled(settings):
            raise OperationError("Enable Guest Mode for this bot in BotFather first.")
    path = settings.data_dir / RELATIVE_POLICY
    if (
        not state.error
        and state.enabled is enabled
        and state.owner_agent is owner_agent
        and state.schema == schema
        and path.is_file()
    ):
        return {
            "enabled": enabled,
            "changed": False,
            "restart_required": False,
            "owner_only": True,
            "owner_agent": owner_agent,
        }
    if path.parent.resolve() != path.parent:
        raise OperationError("Refusing a symlinked guest configuration directory.")
    checkpoint = None
    value = {"schema": schema, "enabled": enabled, "revision": secrets.token_hex(16)}
    if schema >= 3:
        value["owner_agent"] = owner_agent
    body = (json.dumps(value) + "\n").encode()
    uid, gid = identity(settings)

    def write_policy(home):
        target = home / RELATIVE_POLICY
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(target, body)
        os.chown(target, uid, gid)

    if enabled:
        checkpoint = snapshot(runtime, automatic=True)
        smoke(runtime, checkpoint, prepare_copy=write_policy)
    # Revocation deliberately needs neither an idle queue nor a model/network call.
    # A fresh epoch also invalidates all work admitted before a disable/re-enable.
    write_policy(settings.data_dir)
    result = {"enabled": enabled, "changed": True, "restart_required": False, "owner_only": True}
    result["owner_agent"] = owner_agent
    if checkpoint is not None:
        result["checkpoint"] = str(checkpoint)
    return result
