"""M2: intent -> intake -> standards -> planner -> HUMAN approval. Nothing executes before it."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from aloop_strands import approvals, dashboard, pins, supervisor
from aloop_strands.events import EventLog
from aloop_strands.gates import plan_gates
from aloop_strands.gates.base import GateContext
from aloop_strands.gates.tool_gates import role_files_gate
from aloop_strands.scripted import ScriptedModel

INTENT = {"actor": "a developer", "want": "a semver parser", "why": "compare versions",
          "success": ["parse('1.2.3') == (1, 2, 3)"], "failure": ["parse('x') raises ValueError"]}
STANDARDS = "# Standards\n\nlanguage: python\ntest_command: python -m pytest -q\n"
PLAN = {"sprint": 1, "workflow": "tdd", "stories": [{"id": "S1-01", "title": "Parse versions", "tasks": [
    {"id": "S1-01-T1", "title": "parse MAJOR.MINOR.PATCH", "tests": ["tests/test_semver.py"],
     "targets": ["src/semver.py"]}]}]}


def write(path, content):
    return {"tool": "write_artifact", "input": {"path": path, "content": content}}


def planning_models():
    return {
        "intake": ScriptedModel([write("docs/agents/intent.json", json.dumps(INTENT)), "intent recorded"]),
        "standards": ScriptedModel([write("docs/agents/standards.md", STANDARDS), "standards written"]),
        "planner": ScriptedModel([write("docs/agents/sprint1/plan.json", json.dumps(PLAN)), "plan written"]),
    }


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / "workflows").mkdir(parents=True)
    (root / "workflows" / "tdd.yaml").write_text("name: tdd\n")
    pins.init(root)
    return root


def _planned(root):
    sup = supervisor.Supervisor(root, models=planning_models())
    return sup, sup.plan("a semver parser")


# --- planning gates ------------------------------------------------------------
def test_plan_reaches_awaiting_approval_and_dispatches_nothing(proj):
    sup, state = _planned(proj)
    assert state.status == "awaiting_approval" and len(state.code) == 8
    kinds = [e["kind"] for e in EventLog(proj).query()]
    assert "dispatch" not in kinds or all(e["role"] in ("intake", "standards", "planner")
                                          for e in EventLog(proj).query(kind="dispatch"))


def test_execution_dispatch_denied_before_approval(proj):
    sup, state = _planned(proj)
    v = sup.dispatch_gate("red", "S1-01-T1")
    assert not v.allow and v.rule == "gate.approval"


def test_wrong_code_is_refused_and_logged(proj):
    sup, state = _planned(proj)
    with pytest.raises(approvals.ApprovalRefused):
        approvals.approve(proj, sprint=1, typed="00000000", actor="human:cli")
    assert any(e["kind"] == "approval.refused" for e in EventLog(proj).query())
    assert not sup.dispatch_gate("red", "S1-01-T1").allow


def test_right_code_allows_only_planned_tasks(proj):
    sup, state = _planned(proj)
    approvals.approve(proj, sprint=1, typed=state.code, actor="human:cli")
    assert sup.dispatch_gate("red", "S1-01-T1").allow
    v = sup.dispatch_gate("red", "S1-09-T9")
    assert not v.allow and "not in the approved plan" in v.reason


def test_plan_change_after_approval_voids_it(proj):
    sup, state = _planned(proj)
    approvals.approve(proj, sprint=1, typed=state.code, actor="human:cli")
    p = proj / "docs" / "agents" / "sprint1" / "plan.json"
    p.write_text(p.read_text().replace("parse MAJOR", "parse anything"))
    assert not sup.dispatch_gate("green", "S1-01-T1").allow


@pytest.mark.parametrize("f", ["intent.json", "standards.md"])
def test_intent_or_standards_change_voids_approval(proj, f):
    sup, state = _planned(proj)
    approvals.approve(proj, sprint=1, typed=state.code, actor="human:cli")
    (proj / "docs" / "agents" / f).write_text((proj / "docs" / "agents" / f).read_text() + " ")
    assert not sup.dispatch_gate("green", "S1-01-T1").allow


def test_tampered_log_voids_approval(proj):
    sup, state = _planned(proj)
    approvals.approve(proj, sprint=1, typed=state.code, actor="human:cli")
    log = EventLog(proj)
    log.path.write_text(log.path.read_text().replace('"approval.granted"', '"approval.grantee"'))
    assert not sup.dispatch_gate("green", "S1-01-T1").allow


def test_pins_change_blocks_dispatch_even_when_approved(proj):
    sup, state = _planned(proj)
    approvals.approve(proj, sprint=1, typed=state.code, actor="human:cli")
    (proj / "workflows" / "tdd.yaml").write_text("name: anything\n")
    v = sup.dispatch_gate("red", "S1-01-T1")
    assert not v.allow and v.rule == "gate.pins"


def test_planning_roles_need_no_approval(proj):
    sup = supervisor.Supervisor(proj, models={})
    assert sup.dispatch_gate("intake", None).allow


def test_unknown_role_dispatch_denied(proj):
    sup = supervisor.Supervisor(proj, models={})
    assert not sup.dispatch_gate("root", None).allow


# --- per-artifact gates ---------------------------------------------------------
def test_intake_gate_requires_five_parts(proj):
    d = proj / "docs" / "agents"
    d.mkdir(parents=True)
    (d / "intent.json").write_text(json.dumps({k: v for k, v in INTENT.items() if k != "failure"}))
    v = plan_gates.intake_verify(proj)
    assert not v.allow and "failure" in v.reason


def test_standards_gate_requires_test_command(proj):
    d = proj / "docs" / "agents"
    d.mkdir(parents=True)
    (d / "standards.md").write_text("# Standards\nlanguage: python\n")
    assert not plan_gates.standards_verify(proj).allow


@pytest.mark.parametrize("mutate", [
    lambda p: p["stories"][0]["tasks"][0].pop("tests"),
    lambda p: p["stories"][0]["tasks"][0].update(id="T1"),
    lambda p: p["stories"][0]["tasks"][0].update(targets=["../../etc/passwd"]),
    lambda p: p["stories"][0]["tasks"][0].update(targets=["gates/role.yaml"]),
    lambda p: p.update(workflow="yolo"),
    lambda p: p["stories"].clear(),
])
def test_plan_gate_rejects_bad_plans(proj, mutate):
    import copy
    p = copy.deepcopy(PLAN)
    mutate(p)
    d = proj / "docs" / "agents" / "sprint1"
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(p))
    assert not plan_gates.plan_verify(proj, 1).allow


def test_planning_agent_cannot_write_outside_docs_agents(proj):
    c = GateContext(role="planner", task="plan", project_root=proj, worktree=proj, tool="write_artifact",
                    input={"path": "src/evil.py"}, log=EventLog(proj))
    v = role_files_gate(c)
    assert not v.allow and v.rule == "gate.artifact_scope"


def test_failed_planning_gate_stops_before_approval(proj):
    models = planning_models()
    models["planner"] = ScriptedModel([write("docs/agents/sprint1/plan.json", "{}"), "plan written"])
    state = supervisor.Supervisor(proj, models=models).plan("a semver parser")
    assert state.status == "blocked" and state.code is None


# --- dashboard -------------------------------------------------------------------
def test_dashboard_shows_plan_and_approves_with_code(proj):
    sup, state = _planned(proj)
    srv = dashboard.make_server(proj, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert state.code in page and "S1-01-T1" in page
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(base + "/approve", data=b"sprint=1&code=deadbeef"))
        assert e.value.code == 403
        r = urllib.request.urlopen(urllib.request.Request(base + "/approve", data=f"sprint=1&code={state.code}".encode()))
        assert r.status == 200
        assert sup.dispatch_gate("red", "S1-01-T1").allow
        assert any(e["kind"] == "approval.granted" and e["actor"] == "human:dashboard" for e in EventLog(proj).query())
    finally:
        srv.shutdown()


def test_dashboard_binds_loopback_only(proj):
    srv = dashboard.make_server(proj, port=0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()
