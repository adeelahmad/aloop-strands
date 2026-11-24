"""Gates as a Strands intervention handler.

Interventions: https://strandsagents.com/docs/user-guide/sdk/agents/interventions/
- `before_tool_call` returning Deny sets `cancel_tool`; the tool never runs and the model sees
  the reason as an error result.
- `before_invocation` returning Deny sets `BeforeInvocationEvent.cancel` and the invocation
  ends before the first model call (used for the pins check).
- `on_error="deny"` makes a crash in this handler a Deny (fail closed); the SDK default
  "throw" also blocks, "proceed" would fail open and is never used.
"""
from __future__ import annotations

from pathlib import Path

from strands.interventions import Deny, InterventionHandler, Proceed

from .. import pins
from ..events import EventLog
from . import base, tool_gates


class GateIntervention(InterventionHandler):
    def __init__(self, role: str, task: str, worktree: Path, project_root: Path):
        self.role, self.task = role, task
        self.worktree, self.project_root = Path(worktree), Path(project_root)
        self.log = EventLog(self.project_root)

    @property
    def name(self) -> str:
        return "aloop-gates"

    @property
    def on_error(self):
        return "deny"

    def _ctx(self, tool: str, inp) -> base.GateContext:
        return base.GateContext(role=self.role, task=self.task, project_root=self.project_root,
                                worktree=self.worktree, tool=tool,
                                input=inp if isinstance(inp, dict) else {"path": inp}, log=self.log)

    def before_invocation(self, event, **kwargs):
        def pins_gate(_ctx):
            diff = pins.verify(self.project_root)
            if diff:
                return base.Verdict.deny("gate.pins", f"gate/workflow files changed without approval: {diff}")
            return base.Verdict.ok("gate.pins")
        v = base.evaluate([pins_gate], self._ctx("<start>", {}))
        return Proceed() if v.allow else Deny(reason=f"{v.rule}: {v.reason}")

    def before_tool_call(self, event, **kwargs):
        tu = event.tool_use
        # Read the module attribute each call so the gate list cannot be captured stale.
        v = base.evaluate(list(tool_gates.TOOL_GATES), self._ctx(tu.get("name", ""), tu.get("input") or {}))
        return Proceed() if v.allow else Deny(reason=f"{v.rule}: {v.reason}")
