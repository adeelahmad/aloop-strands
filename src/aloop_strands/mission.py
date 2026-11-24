"""Mission layer (MAPE-K): after a goal completes, Monitor the run, Analyze/Plan a next goal with the
mission_planner role, and stop. Proposing is a two-way door; a goal whose actions are one-way waits for
a human; executing any next goal still needs planning and the human plan gate."""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import budget, doors, roles
from .gates import base


@dataclass
class Proposal:
    status: str          # proposed | needs_human | refused | blocked
    goal: dict | None = None
    door: str | None = None
    reason: str = ""


def _verify(goal) -> base.Verdict:
    if not isinstance(goal, dict):
        return base.Verdict.deny("gate.mission", "next_goal.json is not an object")
    if not isinstance(goal.get("intent"), str) or not goal["intent"].strip():
        return base.Verdict.deny("gate.mission", "next goal has no intent")
    if not goal.get("rationale") or not isinstance(goal.get("actions"), list) or not goal["actions"]:
        return base.Verdict.deny("gate.mission", "next goal needs a rationale and actions")
    return base.Verdict.ok("gate.mission")


def propose_next(sup) -> Proposal:
    log = sup.log
    if not list(log.query(kind="sprint.done", sprint=sup.sprint)):
        log.append("mission.refused", reason=f"sprint {sup.sprint} has not completed")
        return Proposal("refused", reason="the current goal is not complete")
    # Monitor + Knowledge: facts from the run log, not from any agent's words.
    denials = sum(1 for e in log.query(kind="gate.decision") if e["verdict"] == "deny")
    log.append("mission.monitor", sprint=sup.sprint, merged=len(list(log.query(kind="merge"))),
               denials=denials, tokens=budget.run_used(log))
    v = sup.dispatch_gate("mission_planner", None)
    if not v.allow:
        return Proposal("blocked", reason=f"{v.rule}: {v.reason}")
    agent = roles.build_agent("mission_planner", model=sup.model_for("mission_planner"), task="mission",
                              worktree=sup.root, project_root=sup.root)
    agent("The last goal is merged and verified. Propose ONE next goal in docs/agents/next_goal.json "
          "with intent, rationale and actions.")
    f = sup.root / "docs" / "agents" / "next_goal.json"
    try:
        goal = json.loads(f.read_text())
    except (OSError, ValueError) as e:
        goal = None
        v = base.Verdict.deny("gate.mission", f"next_goal.json unreadable: {e}")
    else:
        v = _verify(goal)
    ctx = base.GateContext(role="mission_planner", task="mission", project_root=sup.root, worktree=sup.root,
                           tool="<stop>", input={}, log=log)
    v = base.evaluate([lambda _c: v], ctx)
    if not v.allow:
        return Proposal("blocked", reason=f"{v.rule}: {v.reason}")
    door = doors.classify(goal)
    status = "needs_human" if door else "proposed"
    log.append("mission.proposed", goal=goal, door=door, status=status)
    return Proposal(status, goal, door)
