"""Tool-free application service, composed from protocol, history and model boundaries."""

import hmac
import logging
import os
import re
import threading
import time
from pathlib import Path

from .history import History
from .http import Server
from .instructions import AUDIENCES, configured_instructions
from .protocol import ROUTES, ReplyRequest, request_id


class Service:
    def __init__(self, home, key, *, skills, generate=None, clock=time.time, prompt):
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
        self.generate = generate or model_generate
        self.history = History(home, clock=clock)
        self.capacity = {route: threading.BoundedSemaphore(1) for route in ROUTES}

    def authorized(self, authorization):
        return isinstance(authorization, str) and hmac.compare_digest(
            authorization.encode(), ("Bearer " + self.key).encode()
        )

    def reply(self, payload):
        request = ReplyRequest.parse(payload)
        capacity = self.capacity[request.route]
        if not capacity.acquire(blocking=False):
            raise BlockingIOError("Service capacity reached.")
        try:
            cached, history = self.history.begin(request)
            if cached is not None:
                return cached
            messages = [
                {"role": "system", "content": self.prompt + "\n" + self.skills[request.audience]}
            ]
            for question, answer in history:
                messages.extend(
                    [
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": answer},
                    ]
                )
            messages.append({"role": "user", "content": request.text})
            try:
                answer = self.generate(messages)
            except Exception:
                raise RuntimeError("Model unavailable; private diagnostics omitted.") from None
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 3000:
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
    )
    Server(("0.0.0.0", 9120), service).serve_forever()


if __name__ == "__main__":
    main()
