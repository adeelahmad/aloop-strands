"""A deterministic Strands model that replays a script of turns.

Used for CI (zero tokens) and for negative e2e runs where a "worker" must attempt a forbidden
action. Implements the `strands.models.Model` interface; the stream event shapes follow
https://strandsagents.com/docs/user-guide/sdk/model-providers/custom-model-provider/ .
Each turn is either a string (final text) or a dict {"tool": name, "input": {...}}.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterable

from strands.models import Model


class ScriptedModel(Model):
    def __init__(self, turns: list[Any]):
        self.turns = list(turns)
        self.calls = 0
        self.seen: list[Any] = []  # messages the model received, per call

    def update_config(self, **model_config: Any) -> None:  # noqa: D401
        pass

    def get_config(self) -> Any:
        return {"model_id": "scripted"}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs) -> AsyncIterable[dict]:
        self.seen.append(messages)
        turn = self.turns[self.calls] if self.calls < len(self.turns) else "done"
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if isinstance(turn, dict):
            yield {"contentBlockStart": {"start": {"toolUse": {"name": turn["tool"], "toolUseId": f"t{self.calls}"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(turn.get("input", {}))}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": str(turn)}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
                            "metrics": {"latencyMs": 1}}}
