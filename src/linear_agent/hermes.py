"""Native resumable Runs API; client messages cannot select tools, keys or endpoints."""

import re

from .transport import request_json


class Hermes:
    def __init__(self, *, request=request_json):
        self.request = request

    def call(self, agent, path, *, body=None, headers=None):
        return self.request(
            agent.executor_url + path,
            method="GET" if body is None else "POST",
            body=body,
            headers={"Authorization": "Bearer " + agent.executor_key, **(headers or {})},
        )

    def start(self, agent, session, text, identity):
        return self.call(
            agent,
            "/v1/runs",
            body={"session_id": session, "input": text},
            headers={"Idempotency-Key": "linear-" + identity},
        )

    def ready(self, agent):
        capabilities = self.call(agent, "/v1/capabilities")
        inventory = self.call(agent, "/v1/toolsets")
        tools = inventory.get("data") if isinstance(inventory, dict) else None
        if not isinstance(capabilities, dict) or not isinstance(tools, list):
            raise ValueError("Unrecognized executor protocol.")
        features = capabilities.get("features", {})
        idempotency = features.get("runs_idempotency", {})
        if (
            features.get("run_submission") is not True
            or idempotency.get("supported") is not True
            or idempotency.get("durable") is not True
        ):
            raise PermissionError("Durable native run admission is required.")
        enabled = {item["name"] for item in tools if item.get("enabled")}
        if enabled != {"web", "clarify", "council-linear"}:
            raise PermissionError("Executor tools exceed the reviewed Linear policy.")

    def run(self, agent, run_id):
        return self.call(agent, "/v1/runs/" + self.run_id(run_id))

    def stop(self, agent, run_id):
        return self.call(agent, "/v1/runs/" + self.run_id(run_id) + "/stop", body={})

    def clarify(self, agent, run_id, control, answer):
        if (
            control.get("kind") != "clarify"
            or not isinstance(control.get("prompt_id"), str)
            or type(control.get("execution_generation")) is not int
        ):
            raise ValueError("A current clarification identity is required.")
        return self.call(
            agent,
            "/v1/runs/" + self.run_id(run_id) + "/clarify",
            body={
                "request_id": control["prompt_id"],
                "execution_generation": control["execution_generation"],
                "answer": answer,
            },
        )

    @staticmethod
    def run_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"run_[a-f0-9]{32}", value):
            raise ValueError("Invalid native run ID.")
        return value
