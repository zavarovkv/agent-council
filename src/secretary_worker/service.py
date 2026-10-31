"""Tool-free application service, composed from protocol, history and model boundaries."""

import hmac
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from .history import History
from .http import Server
from .instructions import AUDIENCES, configured_instructions, guest_instructions
from .protocol import ROUTES, ReplyRequest, request_id


class Service:
    def __init__(
        self, home, key, *, skills, generate=None, clock=time.time, prompt, guest_prompt=None
    ):
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("A dedicated 256-bit service key is required.")
        from .model import generate as model_generate

        if set(skills) != AUDIENCES or any(
            not isinstance(body, str) or not body.strip() for body in skills.values()
        ):
            raise ValueError("All secretary skills are required.")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("A nonempty secretary policy is required.")
        self.key, self.skills, self.prompt = key, dict(skills), prompt
        if guest_prompt is not None and (
            not isinstance(guest_prompt, str) or not 0 < len(guest_prompt.strip()) <= 16000
        ):
            raise ValueError("A valid public guest policy is required.")
        self.guest_prompt = guest_prompt
        self.generate = generate or model_generate
        self.history = History(home, clock=clock)
        self.capacity = {route: threading.BoundedSemaphore(1) for route in ROUTES}

    def authorized(self, authorization):
        return isinstance(authorization, str) and hmac.compare_digest(
            authorization.encode(), ("Bearer " + self.key).encode()
        )

    def reply(self, payload):
        request = ReplyRequest.parse(payload)
        if request.mode == "guest" and self.guest_prompt is None:
            raise ValueError("Guest inference is unavailable.")
        capacity = self.capacity[request.route]
        if not capacity.acquire(blocking=False):
            raise BlockingIOError("Service capacity reached.")
        try:
            cached, history = self.history.begin(request)
            if cached is not None:
                return cached
            messages = [
                {
                    "role": "system",
                    "content": (
                        self.guest_prompt
                        if request.mode == "guest"
                        else self.prompt + "\n" + self.skills[request.audience]
                    ),
                }
            ]
            for question, answer in history:
                messages.extend(
                    [
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": answer},
                    ]
                )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        json.dumps(
                            {"invocation": request.text, "quoted_message": request.reply_context},
                            ensure_ascii=False,
                        )
                        if request.mode == "guest"
                        else request.text
                    ),
                }
            )
            try:
                answer = self.generate(messages)
            except Exception as error:
                status = getattr(error, "status_code", None)
                category = (
                    "provider_auth"
                    if status in (401, 403)
                    else "provider_quota"
                    if status == 429
                    else "provider_timeout"
                    if isinstance(error, TimeoutError)
                    else "model_unavailable"
                )
                self.history.failed(request.identifier, category)
                raise RuntimeError("Model unavailable; private diagnostics omitted.") from None
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 3000:
                self.history.failed(request.identifier, "invalid_reply")
                raise RuntimeError("Invalid final reply.")
            self.history.complete(request.identifier, answer)
            return answer
        finally:
            capacity.release()

    def acknowledge(self, payload):
        self.history.acknowledge(request_id(payload))


def main():
    os.umask(0o077)
    logging.disable(logging.CRITICAL)  # Provider exceptions may contain private request bodies.
    home = Path(os.environ["HERMES_HOME"])
    if home != Path("/opt/data") or os.geteuid() == 0:
        raise SystemExit("The isolated worker requires a non-root user and its own data mount.")
    owner_name = os.environ.get("COUNCIL_SECRETARY_OWNER_NAME", "владельца аккаунта")
    assistant_name = os.environ.get("COUNCIL_SECRETARY_ASSISTANT_NAME", "Ассистент")
    instructions = configured_instructions(home, os.environ)
    service = Service(
        home,
        os.environ.get("COUNCIL_SECRETARY_KEY", ""),
        skills=instructions.skills,
        prompt=f"Твоё имя: {assistant_name}. Ты личный помощник {owner_name}.\n"
        + instructions.policy,
        guest_prompt=guest_instructions(),
    )
    Server(("0.0.0.0", 9120), service).serve_forever()


if __name__ == "__main__":
    main()
