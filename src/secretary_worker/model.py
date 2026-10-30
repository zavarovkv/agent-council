"""Use the pinned Hermes provider router; never create an agent or execute tools."""

import os
from pathlib import Path


def generate(messages):
    from agent.auxiliary_client import call_llm
    from hermes_cli.config import load_env, read_user_config_raw

    load_env()  # Install only this mounted profile's native secret scope.
    config = read_user_config_raw(Path(os.environ["HERMES_HOME"]) / "config.yaml")
    selected = config.get("model", {})
    if (
        not isinstance(selected, dict)
        or not selected.get("provider")
        or not selected.get("default")
    ):
        raise ValueError("Configure this service's own model account first.")
    response = call_llm(
        provider=selected["provider"],
        model=selected["default"],
        messages=messages,
        tools=None,
        timeout=45,
        max_tokens=2000,
    )
    # Only final text. Never fall back to reasoning fields or execute returned tool calls.
    if isinstance(response, str):
        text = response
    else:
        choices = (
            response.get("choices")
            if isinstance(response, dict)
            else getattr(response, "choices", None)
        )
        message = response
        if choices:
            message = (
                choices[0].get("message", {})
                if isinstance(choices[0], dict)
                else choices[0].message
            )
        if isinstance(message, dict):
            if message.get("tool_calls"):
                raise ValueError("Tool calls are unsupported.")
            text = message.get("content", "")
        else:
            if getattr(message, "tool_calls", None):
                raise ValueError("Tool calls are unsupported.")
            text = getattr(message, "content", "")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("The model returned no final reply.")
    return text.strip()[:3000]
