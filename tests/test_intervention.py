"""Gates fire through the real Strands agent loop for every role, and fail closed."""
import pytest

from aloop_strands import pins, roles
from aloop_strands.events import EventLog
from aloop_strands.scripted import ScriptedModel

WORKER_ROLES = ["intake", "standards", "planner", "red", "scaffold", "green",
                "structural_review", "final_verification"]


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / "gates").mkdir(parents=True)
    (root / "gates" / "g.yaml").write_text("x\n")
    wt = root / ".worktrees" / "T1"
    for d in ("src", "tests", "gates"):
        (wt / d).mkdir(parents=True)
    (wt / "gates" / "g.yaml").write_text("x\n")
    pins.init(root)
    return root, wt


def build(proj, role, turns):
    root, wt = proj
    m = ScriptedModel(turns)
    return roles.build_agent(role, model=m, task="T1", worktree=wt, project_root=root), m


@pytest.mark.parametrize("role", WORKER_ROLES + ["supervisor"])
def test_every_role_has_fail_closed_gates(proj, role):
    agent, _ = build(proj, role, ["done"])
    handlers = agent._intervention_registry.handlers
    assert any(h.name == "aloop-gates" for h in handlers)
    assert all(h.on_error != "proceed" for h in handlers)


def test_unknown_role_cannot_be_built(proj):
    with pytest.raises(ValueError):
        build(proj, "freelancer", ["done"])


def test_supervisor_has_no_write_tools(proj):
    agent, _ = build(proj, "supervisor", ["done"])
    assert not {"write_file", "edit_file", "run_tests", "shell"} & set(agent.tool_names)


def _last_tool_result(m):
    return m.seen[-1][-1]["content"][0]["toolResult"]


def test_green_worker_cannot_edit_its_gate(proj):
    root, wt = proj
    agent, m = build(proj, "green", [{"tool": "write_file", "input": {"path": "gates/g.yaml", "content": "allow all"}}, "done"])
    agent("make tests pass")
    assert (wt / "gates" / "g.yaml").read_text() == "x\n"
    res = _last_tool_result(m)
    assert res["status"] == "error" and "gate.integrity" in res["content"][0]["text"]
    assert any(e["rule"] == "gate.integrity" and e["verdict"] == "deny"
               for e in EventLog(root).query(kind="gate.decision"))


def test_worker_cannot_write_outside_worktree(proj, tmp_path):
    root, wt = proj
    target = tmp_path / "escaped.py"
    agent, m = build(proj, "green", [{"tool": "write_file", "input": {"path": str(target), "content": "x"}}, "done"])
    agent("go")
    assert not target.exists()
    assert "gate.scope" in _last_tool_result(m)["content"][0]["text"]


def test_allowed_write_happens_and_is_logged(proj):
    root, wt = proj
    agent, m = build(proj, "red", [{"tool": "write_file", "input": {"path": "tests/test_a.py", "content": "def test_a(): assert 0\n"}}, "done"])
    agent("write tests")
    assert (wt / "tests" / "test_a.py").exists()
    assert any(e["verdict"] == "allow" and e["tool"] == "write_file"
               for e in EventLog(root).query(kind="gate.decision"))


def test_crashing_gate_blocks_tool(proj, monkeypatch):
    root, wt = proj
    from aloop_strands.gates import tool_gates
    def boom(ctx):
        raise RuntimeError("gate bug")
    monkeypatch.setattr(tool_gates, "TOOL_GATES", [boom] + list(tool_gates.TOOL_GATES))
    agent, m = build(proj, "red", [{"tool": "write_file", "input": {"path": "tests/test_a.py", "content": "x"}}, "done"])
    agent("go")
    assert not (wt / "tests" / "test_a.py").exists()


def test_tampered_pins_cancel_the_worker_before_any_tool(proj):
    root, wt = proj
    (root / "gates" / "g.yaml").write_text("allow all\n")
    agent, m = build(proj, "red", [{"tool": "write_file", "input": {"path": "tests/test_a.py", "content": "x"}}, "done"])
    agent("go")
    assert not (wt / "tests" / "test_a.py").exists()
    assert m.calls == 0
    assert any(e["rule"] == "gate.pins" for e in EventLog(root).query(kind="gate.decision"))
