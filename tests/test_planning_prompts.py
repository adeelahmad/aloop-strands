"""Planning prompts carry an exact example of each file, and every example passes its own gate
(small local models copy the shape; a template that fails its gate would teach failure)."""
from aloop_strands import supervisor
from aloop_strands.gates import plan_gates


def test_every_planning_template_passes_its_gate(tmp_path):
    t = supervisor.planning_templates(sprint=1)
    d = tmp_path / "docs" / "agents"
    (d / "sprint1").mkdir(parents=True)
    (d / "intent.json").write_text(t["intake"])
    (d / "standards.md").write_text(t["standards"])
    (d / "sprint1" / "plan.json").write_text(t["planner"])
    assert plan_gates.intake_verify(tmp_path).allow
    assert plan_gates.standards_verify(tmp_path).allow
    assert plan_gates.plan_verify(tmp_path, 1).allow


def test_prompts_embed_templates_and_paths():
    p = supervisor.planning_prompts("a semver parser", sprint=2)
    t = supervisor.planning_templates(sprint=2)
    for role in ("intake", "standards", "planner"):
        assert t[role] in p[role] and "write_artifact" in p[role]
    assert "docs/agents/sprint2/plan.json" in p["planner"] and "a semver parser" in p["intake"]
