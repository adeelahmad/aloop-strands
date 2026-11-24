"""Budgets as gates (aloop's time box). Usage comes from the model's own metadata via
`agent.event_loop_metrics.accumulated_usage` (https://strandsagents.com/docs/user-guide/sdk/observability-evaluation/metrics/).
The check runs before every model call (BeforeModelCallEvent.cancel through an intervention Deny,
https://strandsagents.com/docs/user-guide/sdk/agents/interventions/), so the overshoot is at most one
model call. Strands has no built-in USD cap; tokens are the unit here."""
from __future__ import annotations

import os
from dataclasses import dataclass

from strands.interventions import Deny, InterventionHandler, Proceed

from .events import EventLog
from .gates import base

EXECUTION_ROLES = {"red", "scaffold", "green", "developer", "structural_review", "final_verification"}


@dataclass(frozen=True)
class Budgets:
    attempt_tokens: int = int(os.environ.get("ALOOP_ATTEMPT_TOKENS", "400000"))
    run_tokens: int = int(os.environ.get("ALOOP_RUN_TOKENS", "5000000"))


def run_used(log: EventLog) -> int:
    return sum(int(e.get("total_tokens", 0)) for e in log.query(kind="usage") if e.get("role") in EXECUTION_ROLES)


class BudgetIntervention(InterventionHandler):
    def __init__(self, budgets: Budgets, log: EventLog, role: str, task: str, worktree):
        self.budgets, self.log, self.role, self.task, self.worktree = budgets, log, role, task, worktree
        self.breached: base.Verdict | None = None
        self.run_before = run_used(log) if role in EXECUTION_ROLES else 0

    @property
    def name(self) -> str:
        return "aloop-budget"

    @property
    def on_error(self):
        return "deny"

    def _used(self, agent) -> int:
        return int(agent.event_loop_metrics.accumulated_usage["totalTokens"])

    def _check(self, agent) -> base.Verdict:
        if self.breached:
            return self.breached
        used = self._used(agent)

        def gate(_ctx):
            if used >= self.budgets.attempt_tokens:
                return base.Verdict.deny("gate.budget.attempt", f"attempt used {used} tokens, hard limit {self.budgets.attempt_tokens}")
            if self.role in EXECUTION_ROLES and self.run_before + used >= self.budgets.run_tokens:
                return base.Verdict.deny("gate.budget.run", f"run used {self.run_before + used} tokens, hard limit {self.budgets.run_tokens}")
            return base.Verdict.ok("gate.budget")
        v = base.run_gate(gate, None)
        if not v.allow:
            self.breached = v
            ctx = base.GateContext(role=self.role, task=self.task, project_root=self.log.root, worktree=self.worktree,
                                   tool="<model>", input={}, log=self.log)
            base.evaluate([lambda _c: v], ctx)
            self.log.append("budget.breach", task=self.task, role=self.role, rule=v.rule, reason=v.reason, used=used)
        return v

    def before_model_call(self, event, **kwargs):
        v = self._check(event.agent)
        return Proceed() if v.allow else Deny(reason=f"{v.rule}: {v.reason}")

    def before_tool_call(self, event, **kwargs):
        return Proceed() if not self.breached else Deny(reason=f"{self.breached.rule}: {self.breached.reason}")
