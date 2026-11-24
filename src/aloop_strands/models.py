"""Model routes. aloop's roles map to a planning route (strongest) and a worker route (economical).
Strings are "provider/name" as strands-harness resolves them:
https://strandsagents.com/docs/user-guide/harness/configure/model/"""
from __future__ import annotations

import os

from strands_harness.models import resolve_model

PLANNING_ROLES = {"intake", "standards", "planner", "supervisor"}
DEFAULT = "bedrock/global.anthropic.claude-sonnet-4-6"


def route(role: str) -> str:
    key = "ALOOP_MODEL_PLANNING" if role in PLANNING_ROLES else "ALOOP_MODEL_WORKER"
    return os.environ.get(key) or os.environ.get("ALOOP_MODEL") or DEFAULT


def make_model(role: str):
    r = route(role)
    if r.startswith("openai-chat/"):
        # Any OpenAI-compatible Chat Completions endpoint (LM Studio, routers). harness "openai/"
        # uses the Responses API, which such endpoints often lack.
        # https://strandsagents.com/docs/user-guide/concepts/model-providers/openai/
        from strands.models.openai import OpenAIModel
        return OpenAIModel(client_args={"base_url": os.environ.get("OPENAI_BASE_URL"),
                                        "api_key": os.environ.get("OPENAI_API_KEY") or "unused"},
                           model_id=r.split("/", 1)[1])
    return resolve_model(r, default=DEFAULT)


# Non-secret model settings copied into the boxed worker; credentials go only through Box secret.ref.
PASSTHROUGH = ("ALOOP_MODEL", "ALOOP_MODEL_PLANNING", "ALOOP_MODEL_WORKER", "AWS_REGION",
               "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL")


def worker_env() -> dict:
    return {k: v for k, v in os.environ.items() if k in PASSTHROUGH}
