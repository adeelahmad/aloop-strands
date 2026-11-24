"""M4: budgets as gates, the door rule, and the mission layer (MAPE-K next goal)."""
import json
import subprocess

import pytest

from aloop_strands import approvals, budget, doors, mission, project, supervisor
from aloop_strands.events import EventLog
from aloop_strands.scripted import ScriptedModel

import scripted_semver as S


def git(root, *a):
    return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def proj(tmp_path):
    return project.init(tmp_path / "app")


def approved(root, models, **kw):
    sup = supervisor.Supervisor(root, models=models, **kw)
    st = sup.plan("a semver parser")
    assert st.status == "awaiting_approval", st.reason
    approvals.approve(root, 1, st.code, actor="human:test")
    return sup


# --- budgets ------------------------------------------------------------------------
def test_hard_token_budget_stops_worker_and_discards_work(proj):
    spin = [S.w("write_file", "src/semver.py", S.IMPL)] + [{"tool": "list_files", "input": {}}] * 20 + ["done"]
    sup = approved(proj, S.models(green_turns=spin),
                   budgets=budget.Budgets(attempt_tokens=60, run_tokens=10_000))
    r = sup.execute(max_attempts=1)
    assert r.status == "blocked" and "gate.budget" in r.reason
    breaches = list(EventLog(proj).query(kind="budget.breach"))
    assert breaches and breaches[-1]["task"] == "S1-01-T1"
    assert "src/semver.py" not in git(proj, "ls-tree", "-r", "--name-only", "main")
    assert not list(EventLog(proj).query(kind="merge"))
    model = sup.models["green"]
    assert model.calls <= 5  # stopped by the gate, not by running out of script


def test_run_budget_closes_admission(proj):
    sup = approved(proj, S.models(), budgets=budget.Budgets(attempt_tokens=10_000, run_tokens=30))
    r = sup.execute()
    assert r.status == "blocked" and "gate.budget.run" in r.reason
    denied = [e for e in EventLog(proj).query(kind="gate.decision") if e["rule"] == "gate.budget.run"]
    assert denied


def test_usage_is_logged_per_attempt(proj):
    sup = approved(proj, S.models())
    assert sup.execute().status == "done"
    usage = list(EventLog(proj).query(kind="usage"))
    assert {u["role"] for u in usage} >= {"red", "scaffold", "green"}
    assert all(u["total_tokens"] > 0 and u["estimated"] is False for u in usage)


def test_budget_gate_crash_blocks(proj, monkeypatch):
    monkeypatch.setattr(budget.BudgetIntervention, "_used", lambda self, agent: 1 / 0)
    sup = approved(proj, S.models(), budgets=budget.Budgets(attempt_tokens=10_000, run_tokens=10_000))
    assert sup.execute(max_attempts=1).status == "blocked"


# --- doors ---------------------------------------------------------------------------
@pytest.mark.parametrize("title,door", [
    ("deploy the parser to production", "deploy"),
    ("publish a release to PyPI", "release"),
    ("push to the default branch", "push_default"),
    ("drop the users table", "data_delete"),
    ("rotate the API secret", "secret"),
    ("parse versions", None),
])
def test_classify(title, door):
    assert doors.classify({"title": title, "targets": ["src/x.py"]}) == door


def test_workflow_target_is_one_way():
    assert doors.classify({"title": "tidy", "targets": [".github/workflows/ci.yml"]}) == "ci_config"


def test_gate_target_is_a_wall():
    assert doors.is_wall(doors.classify({"title": "loosen", "targets": ["gates/role.yaml"]}))


def _plan_with(proj, title):
    plan = json.loads(json.dumps(S.PLAN))
    plan["stories"][0]["tasks"][0]["title"] = title
    m = S.models()
    m["planner"] = ScriptedModel([S.w("write_artifact", "docs/agents/sprint1/plan.json", json.dumps(plan)), "ok"])
    return approved(proj, m)


def test_one_way_task_waits_for_human_door_authorization(proj):
    sup = _plan_with(proj, "deploy the parser to production")
    v = sup.dispatch_gate("red", "S1-01-T1")
    assert not v.allow and v.rule == "gate.door" and "deploy" in v.reason
    code = doors.code_for(proj, "S1-01-T1", "deploy")
    with pytest.raises(doors.DoorRefused):
        doors.authorize(proj, "S1-01-T1", "deploy", "00000000", actor="human:cli")
    with pytest.raises(doors.DoorRefused):
        doors.authorize(proj, "S1-01-T1", "deploy", code, actor="agent:green")
    doors.authorize(proj, "S1-01-T1", "deploy", code, actor="human:cli")
    assert sup.dispatch_gate("red", "S1-01-T1").allow


def test_two_way_task_is_not_asked(proj):
    sup = _plan_with(proj, "parse versions")
    assert sup.dispatch_gate("red", "S1-01-T1").allow
    assert any(e["kind"] == "door.two_way" for e in EventLog(proj).query())


# --- mission (MAPE-K) ------------------------------------------------------------
NEXT = {"intent": "compare two semver versions", "rationale": "parse exists; comparison is the next user need",
        "actions": ["add compare(a, b) to src/semver.py"]}


def test_mission_proposes_next_goal_after_done(proj):
    sup = approved(proj, {**S.models(), "mission_planner": ScriptedModel(
        [S.w("write_artifact", "docs/agents/next_goal.json", json.dumps(NEXT)), "proposed"])})
    assert sup.execute().status == "done"
    prop = mission.propose_next(sup)
    assert prop.status == "proposed" and prop.goal["intent"] == NEXT["intent"]
    assert prop.door is None  # two-way: proposing is reversible
    kinds = [e["kind"] for e in EventLog(proj).query()]
    assert "mission.monitor" in kinds and "mission.proposed" in kinds
    # nothing executes: the next goal still goes through planning and the human plan gate
    assert not sup.dispatch_gate("red", "S2-01-T1").allow


def test_mission_one_way_goal_needs_human(proj):
    goal = {**NEXT, "intent": "publish a release to PyPI", "actions": ["upload the package"]}
    sup = approved(proj, {**S.models(), "mission_planner": ScriptedModel(
        [S.w("write_artifact", "docs/agents/next_goal.json", json.dumps(goal)), "proposed"])})
    assert sup.execute().status == "done"
    prop = mission.propose_next(sup)
    assert prop.status == "needs_human" and prop.door == "release"


def test_mission_refuses_before_goal_complete(proj):
    sup = supervisor.Supervisor(proj, models=S.models())
    assert mission.propose_next(sup).status == "refused"


def test_mission_rejects_malformed_proposal(proj):
    sup = approved(proj, {**S.models(), "mission_planner": ScriptedModel(
        [S.w("write_artifact", "docs/agents/next_goal.json", "{}"), "proposed"])})
    sup.execute()
    assert mission.propose_next(sup).status == "blocked"
