"""The supervisor: coordinates roles, owns dispatch, never writes. It has no write tools at all
(roles.ROLE_TOOLS["supervisor"]) and every dispatch passes `dispatch_gate`."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import approvals, pins, roles
from .events import EventLog
from .gates import base, plan_gates
from .gates.tool_gates import ROLE_TOOLS

PLANNING = ("intake", "standards", "planner")
EXECUTION = ("red", "scaffold", "green", "structural_review", "final_verification")


@dataclass
class PlanState:
    status: str             # awaiting_approval | blocked
    sprint: int
    code: str | None = None
    reason: str = ""


class Supervisor:
    def __init__(self, root: Path | str, models: dict | None = None, model_factory: Callable | None = None,
                 sprint: int = 1):
        self.root = Path(root)
        self.models = models or {}
        self.model_factory = model_factory
        self.sprint = sprint
        self.log = EventLog(self.root)

    def model_for(self, role: str):
        if role in self.models:
            return self.models[role]
        if self.model_factory:
            return self.model_factory(role)
        raise LookupError(f"no model configured for role {role}")

    # -- dispatch gate ---------------------------------------------------------
    def dispatch_gate(self, role: str, task: str | None) -> base.Verdict:
        def gate(_ctx) -> base.Verdict:
            if role not in ROLE_TOOLS or role == "supervisor":
                return base.Verdict.deny("gate.dispatch", f"unknown role {role!r}")
            diff = pins.verify(self.root)
            if diff:
                return base.Verdict.deny("gate.pins", f"gate/workflow files changed without approval: {diff}")
            if role in PLANNING:
                return base.Verdict.ok("gate.dispatch")
            m = re.match(r"^S(\d+)-", task or "")
            sprint = int(m.group(1)) if m else self.sprint
            ok, why = approvals.approved(self.root, sprint)
            if not ok:
                return base.Verdict.deny("gate.approval", why)
            if role in ("red", "scaffold", "green") and task not in approvals.planned_tasks(self.root, sprint):
                return base.Verdict.deny("gate.approval", f"task {task!r} is not in the approved plan")
            return base.Verdict.ok("gate.dispatch")
        ctx = base.GateContext(role=role, task=task or "-", project_root=self.root, worktree=self.root,
                               tool="dispatch", input={}, log=self.log)
        return base.evaluate([gate], ctx)

    # -- planning ---------------------------------------------------------------
    def _run_planning_role(self, role: str, prompt: str) -> base.Verdict:
        v = self.dispatch_gate(role, None)
        if not v.allow:
            return v
        self.log.append("dispatch", role=role, task="plan")
        agent = roles.build_agent(role, model=self.model_for(role), task="plan", worktree=self.root,
                                  project_root=self.root)
        agent(prompt)
        check = {"intake": lambda: plan_gates.intake_verify(self.root),
                 "standards": lambda: plan_gates.standards_verify(self.root),
                 "planner": lambda: plan_gates.plan_verify(self.root, self.sprint)}[role]
        ctx = base.GateContext(role=role, task="plan", project_root=self.root, worktree=self.root,
                               tool="<stop>", input={}, log=self.log)
        return base.evaluate([lambda _c: check()], ctx)

    def plan(self, intent: str) -> PlanState:
        self.log.append("intent", text=intent, sprint=self.sprint)
        prompts = {
            "intake": f"Intent: {intent}\nWrite docs/agents/intent.json with actor, want, why, success, failure.",
            "standards": "Write docs/agents/standards.md with language and a `test_command:` line.",
            "planner": f"Write docs/agents/sprint{self.sprint}/plan.json: workflow, stories, tasks (id S{self.sprint}-NN-Tk, tests, targets).",
        }
        for role in PLANNING:
            v = self._run_planning_role(role, prompts[role])
            if not v.allow:
                return PlanState("blocked", self.sprint, reason=f"{v.rule}: {v.reason}")
        code = approvals.code_for(self.root, self.sprint)
        self.log.append("plan.ready", sprint=self.sprint, plan_sha=approvals.plan_sha(self.root, self.sprint))
        return PlanState("awaiting_approval", self.sprint, code=code)
