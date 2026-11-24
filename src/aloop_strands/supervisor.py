"""The supervisor: coordinates roles, owns dispatch, never writes. It has no write tools at all
(roles.ROLE_TOOLS["supervisor"]) and every dispatch passes `dispatch_gate`."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import approvals, doors, pins, roles
from .events import EventLog
from .gates import base, plan_gates
from .gates.tool_gates import ROLE_TOOLS

PLANNING = ("intake", "standards", "planner")
NO_APPROVAL = PLANNING + ("mission_planner",)
EXECUTION = ("red", "scaffold", "green", "structural_review", "final_verification")


@dataclass
class PlanState:
    status: str             # awaiting_approval | blocked
    sprint: int
    code: str | None = None
    reason: str = ""


class Supervisor:
    def __init__(self, root: Path | str, models: dict | None = None, model_factory: Callable | None = None,
                 sprint: int = 1, box_bin: str | None = None, model_host: str | None = None,
                 secret_env: str | None = None, budgets=None):
        from .budget import Budgets
        self.budgets = budgets or Budgets()
        self.root = Path(root)
        self.box_bin, self.model_host, self.secret_env = box_bin, model_host, secret_env
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
            if role in NO_APPROVAL:
                return base.Verdict.ok("gate.dispatch")
            m = re.match(r"^S(\d+)-", task or "")
            sprint = int(m.group(1)) if m else self.sprint
            ok, why = approvals.approved(self.root, sprint)
            if not ok:
                return base.Verdict.deny("gate.approval", why)
            if role in ("red", "scaffold", "green", "developer"):
                tasks = approvals.planned_task_map(self.root, sprint)
                if task not in tasks:
                    return base.Verdict.deny("gate.approval", f"task {task!r} is not in the approved plan")
                door = doors.classify(tasks[task])
                if doors.is_wall(door):
                    return base.Verdict.deny("gate.door", f"task {task} is a wall ({door}); it never runs")
                if door and not doors.authorized(self.root, task, door):
                    return base.Verdict.deny("gate.door", f"task {task} crosses the one-way door {door!r}; a human "
                                             f"authorizes it with: aloop-strands door {task} {door} "
                                             f"{doors.code_for(self.root, task, door)}")
                if not door:
                    self.log.append("door.two_way", task=task, reason="reversible: decided, not asked")
            from .budget import run_used
            if run_used(self.log) >= self.budgets.run_tokens:
                return base.Verdict.deny("gate.budget.run", f"run budget {self.budgets.run_tokens} tokens spent; admission closed")
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
        self._log_usage(agent, role, "plan", 1)
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


# ----------------------------------------------------------------------------------------
# Execution (M3): one worktree per task, every step gated, merge only when all gates pass.
# ----------------------------------------------------------------------------------------
import json  # noqa: E402
import shlex  # noqa: E402

from . import project, workflows  # noqa: E402
from .gates import stop_gates as sg  # noqa: E402
from .gates.tool_gates import commit_gate  # noqa: E402
from .tools import local_executor  # noqa: E402
from .worktree import tree_hash  # noqa: E402


@dataclass
class ExecState:
    status: str              # done | blocked
    reason: str = ""
    merged: tuple = ()


def _test_cmd(root: Path) -> str:
    m = re.search(r"^test_command:\s*(.+)$", (root / "docs" / "agents" / "standards.md").read_text(), re.M)
    return m.group(1).strip() if m else "python -m pytest -q"


def _execute(self: Supervisor, max_attempts: int = 3, executor=None) -> ExecState:
    root, sprint = self.root, self.sprint
    if executor is None:
        if self.box_bin:
            from .box import BoxExecutor
            executor = BoxExecutor(self.box_bin, project_root=root, log=self.log)
        else:
            executor = local_executor
    ok, why = approvals.approved(root, sprint)
    if not ok:
        self.log.append("execute.refused", rule="gate.approval", verdict="deny", reason=why)
        return ExecState("blocked", f"gate.approval: {why}")
    plan = json.loads(approvals.plan_files(root, sprint)[2].read_text())
    steps = workflows.load(root, plan["workflow"])
    test_cmd = _test_cmd(root)
    # Record the approved plan on main (content unchanged, so the approval still holds).
    project.git(root, "add", "docs/agents")
    project.git(root, "commit", "-qm", f"plan S{sprint} approved", "--allow-empty")
    merged = []
    for story in plan["stories"]:
        for task in story["tasks"]:
            st = self._run_task(task, [s for s in steps if not s.sprint_level], test_cmd, max_attempts, executor)
            if st.status != "done":
                return ExecState("blocked", st.reason, tuple(merged))
            merged.append(task["id"])
    for step in [s for s in steps if s.sprint_level]:
        v = self._sprint_step(step, test_cmd, max_attempts, executor)
        if not v.allow:
            return ExecState("blocked", f"{v.rule}: {v.reason}", tuple(merged))
    self.log.append("sprint.done", sprint=sprint, merged=merged)
    return ExecState("done", "", tuple(merged))


def _prompt(task: dict, step, why: str | None) -> str:
    p = (f"Task {task['id']}: {task['title']}\nTests: {', '.join(task['tests'])}\n"
         f"Targets: {', '.join(task['targets'])}\nYour step: {step.id} ({step.role}).")
    return p + (f"\nThe gate rejected the last attempt: {why}\nFix exactly that." if why else "")


def _host_commit(self: Supervisor, wt: Path, role: str, task: str, msg: str, expect: str) -> base.Verdict:
    ctx = base.GateContext(role="host", task=task, project_root=self.root, worktree=wt, tool="commit",
                           input={"tree": tree_hash(wt), "expect": expect}, log=self.log)
    v = base.evaluate([commit_gate], ctx)
    if v.allow:
        project.git(wt, "add", "-A")
        project.git(wt, "commit", "-qm", f"{msg}\n\naloop-task: {task}\naloop-role: {role}", "--allow-empty")
    return v


def _stop(self: Supervisor, step, task_id: str, wt: Path, sctx: sg.StopContext) -> base.Verdict:
    ctx = base.GateContext(role=step.role, task=task_id, project_root=self.root, worktree=wt, tool="<stop>",
                           input={}, log=self.log)
    gate = workflows.GATES[step.gate]
    return base.evaluate([lambda _c: gate(sctx)], ctx)


def _run_task(self: Supervisor, task: dict, steps, test_cmd: str, max_attempts: int, executor) -> ExecState:
    tid = task["id"]
    wt = self.root / ".worktrees" / tid
    project.git(self.root, "worktree", "add", "-q", "-b", f"aloop/{tid}", str(wt), "main")
    start = project.git(wt, "rev-parse", "HEAD").strip()
    commits = {"start": start}
    for step in steps:
        v = self.dispatch_gate(step.role, tid)
        if not v.allow:
            return ExecState("blocked", f"{v.rule}: {v.reason}")
        step_base = project.git(wt, "rev-parse", "HEAD").strip()
        why = None
        for attempt in range(1, max_attempts + 1):
            self.log.append("dispatch", role=step.role, task=tid, attempt=attempt, box=bool(self.box_bin))
            bv = self._run_worker(step.role, tid, wt, test_cmd, executor, _prompt(task, step, why), attempt)
            if bv is not None:  # hard budget breach: partial work is discarded, never merged
                project.git(wt, "reset", "-q", "--hard", step_base)
                project.git(wt, "clean", "-qfdx")
                self.log.append("task.blocked", task=tid, step=step.id, reason=f"{bv.rule}: {bv.reason}", discarded=True)
                return ExecState("blocked", f"{tid} {step.id}: {bv.rule}: {bv.reason}")
            base_commit = start if step.role in ("structural_review", "developer") else step_base
            sctx = sg.StopContext(role=step.role, task=tid, worktree=wt, base=base_commit, log=self.log,
                                  test_cmd=shlex.split(test_cmd), red_commit=commits.get("red"), executor=executor)
            v = self._stop(step, tid, wt, sctx)
            if step.role == "structural_review" and v.allow:
                sg._run(sctx)  # host test run of the exact tree to be merged
            expect = "fail" if step.role in ("red", "scaffold") else "pass"
            c = self._host_commit(wt, step.role, tid, f"{tid} {step.id}", expect)
            if v.allow and c.allow:
                commits[step.id] = project.git(wt, "rev-parse", "HEAD").strip()
                break
            why = f"{v.rule}: {v.reason}" if not v.allow else f"{c.rule}: {c.reason}"
        else:
            self.log.append("task.blocked", task=tid, step=step.id, reason=why)
            return ExecState("blocked", f"{tid} {step.id}: {why}")
    return self._merge(tid, wt, test_cmd, executor)


def _merge(self: Supervisor, tid: str, wt: Path, test_cmd: str, executor) -> ExecState:
    project.git(wt, "merge", "-q", "--no-edit", "main")
    sctx = sg.StopContext(role="host", task=tid, worktree=wt, base="main", log=self.log,
                          test_cmd=shlex.split(test_cmd), executor=executor)
    sg._run(sctx)
    v = base.evaluate([commit_gate], base.GateContext(
        role="host", task=tid, project_root=self.root, worktree=wt, tool="commit",
        input={"tree": tree_hash(wt), "expect": "pass"}, log=self.log))
    if not v.allow:
        return ExecState("blocked", f"merge {tid}: {v.rule}: {v.reason}")
    project.git(self.root, "merge", "-q", "--ff-only", f"aloop/{tid}")
    head = project.git(self.root, "rev-parse", "main").strip()
    self.log.append("merge", task=tid, commit=head, rule="gate.merge", verdict="allow", reason="all gates passed")
    project.git(self.root, "worktree", "remove", "--force", str(wt))
    return ExecState("done")


def _sprint_step(self: Supervisor, step, test_cmd: str, max_attempts: int, executor) -> base.Verdict:
    tid = f"S{self.sprint}-FINAL"
    v = self.dispatch_gate(step.role, tid)
    if not v.allow:
        return v
    wt = self.root / ".worktrees" / tid
    project.git(self.root, "worktree", "add", "-q", "--detach", str(wt), "main")
    try:
        self.log.append("dispatch", role=step.role, task=tid, attempt=1, box=bool(self.box_bin))
        bv = self._run_worker(step.role, tid, wt, test_cmd, executor, "Run the full test matrix on main and report.", 1)
        if bv is not None:
            return bv
        sctx = sg.StopContext(role=step.role, task=tid, worktree=wt, base="HEAD", log=self.log,
                              test_cmd=shlex.split(test_cmd), executor=executor)
        return self._stop(step, tid, wt, sctx)
    finally:
        project.git(self.root, "worktree", "remove", "--force", str(wt))


def _log_usage(self: Supervisor, agent, role: str, tid: str, attempt: int) -> None:
    u = agent.event_loop_metrics.accumulated_usage
    self.log.append("usage", role=role, task=tid, attempt=attempt, total_tokens=int(u.get("totalTokens", 0)),
                    input_tokens=int(u.get("inputTokens", 0)), output_tokens=int(u.get("outputTokens", 0)),
                    estimated=False, model=str((agent.model.get_config() or {}).get("model_id", "?")))


def _run_worker(self: Supervisor, role: str, tid: str, wt: Path, test_cmd: str, executor, prompt: str,
                attempt: int = 1):
    """Runs one worker attempt. Returns the budget verdict when the hard limit was breached, else None."""
    if not self.box_bin:
        from .budget import BudgetIntervention
        bi = BudgetIntervention(self.budgets, self.log, role, tid, wt)
        agent = roles.build_agent(role, model=self.model_for(role), task=tid, worktree=wt,
                                  project_root=self.root, test_cmd=test_cmd, executor=executor,
                                  extra_interventions=[bi])
        agent(prompt)
        self._log_usage(agent, role, tid, attempt)
        return bi.breached
    import sys as _sys
    from .events import EventLog as _Log
    from .scripted import ScriptedModel
    outbox = self.root / ".aladdin" / "outbox" / f"{tid}-{role}"
    outbox.mkdir(parents=True, exist_ok=True)
    model = self.models.get(role)
    spec = {"role": role, "task": tid, "worktree": str(wt.resolve()), "outbox": str(outbox.resolve()),
            "test_cmd": test_cmd, "prompt": prompt,
            "budgets": {"attempt_tokens": self.budgets.attempt_tokens,
                        "run_tokens": max(0, self.budgets.run_tokens - __import__("aloop_strands.budget", fromlist=["x"]).run_used(self.log))},
            "script": list(model.turns) if isinstance(model, ScriptedModel) else None}
    r = executor.run(["python", "-m", "aloop_strands.worker", json.dumps(spec)], wt, 1800,
                     extra_rw=[str(outbox.resolve())],
                     model_host=None if spec["script"] is not None else self.model_host,
                     secret_env=self.secret_env)
    reported = list(_Log(outbox).query(kind="gate.decision"))
    self.log.append("worker.exit", role=role, task=tid, exit=r.returncode, box=True,
                    reported_denials=sum(1 for e in reported if e["verdict"] == "deny"))
    last = (r.stdout or "").strip().splitlines()[-1:] or ["{}"]
    try:
        out = json.loads(last[0])
    except ValueError:
        out = {}
    # Usage the worker reports from inside the box is marked as such (the box is the trust boundary).
    self.log.append("usage", role=role, task=tid, attempt=attempt, total_tokens=int(out.get("total_tokens", 0)),
                    estimated=False, reported_by="worker-in-box")
    if out.get("breach"):
        return base.Verdict.deny(out["breach"]["rule"], out["breach"]["reason"])
    return None


Supervisor._run_worker = _run_worker
Supervisor._log_usage = _log_usage
Supervisor.execute = _execute
Supervisor._run_task = _run_task
Supervisor._merge = _merge
Supervisor._host_commit = _host_commit
Supervisor._stop = _stop
Supervisor._sprint_step = _sprint_step
