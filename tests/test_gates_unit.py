"""Unit tests for every tool gate: pass, block, crash-blocks and tamper cases."""
import os
import time

import pytest

from aloop_strands.events import EventLog
from aloop_strands.gates import base, tool_gates as tg


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    wt = root / ".worktrees" / "S1-01-T1"
    for d in (wt / "src", wt / "tests", wt / "gates", root / "gates"):
        d.mkdir(parents=True)
    return root, wt


def ctx(proj, role="green", tool="write_file", **inp):
    root, wt = proj
    return base.GateContext(role=role, task="S1-01-T1", project_root=root, worktree=wt,
                            tool=tool, input=inp, log=EventLog(root))


# --- role gate ---------------------------------------------------------------
def test_role_gate_allows_listed_tool(proj):
    assert tg.role_gate(ctx(proj, role="green", tool="write_file")).allow


@pytest.mark.parametrize("tool", ["write_file", "edit_file", "run_tests", "shell", "commit"])
def test_supervisor_can_never_write_or_run(proj, tool):
    v = tg.role_gate(ctx(proj, role="supervisor", tool=tool))
    assert not v.allow and v.rule == "gate.role"


def test_unknown_role_is_denied(proj):
    assert not tg.role_gate(ctx(proj, role="hacker", tool="read_file")).allow


@pytest.mark.parametrize("role", ["structural_review", "final_verification"])
def test_reviewers_are_read_only(proj, role):
    assert not tg.role_gate(ctx(proj, role=role, tool="write_file")).allow
    assert tg.role_gate(ctx(proj, role=role, tool="read_file")).allow


# --- scope gate --------------------------------------------------------------
def test_scope_allows_inside_worktree(proj):
    assert tg.scope_gate(ctx(proj, path="src/semver.py")).allow


@pytest.mark.parametrize("path", ["/etc/passwd", "../../../outside.py", "src/../../x.py"])
def test_scope_denies_escape(proj, path):
    v = tg.scope_gate(ctx(proj, path=path))
    assert not v.allow and v.rule == "gate.scope"


def test_scope_denies_symlink_escape(proj, tmp_path):
    root, wt = proj
    (tmp_path / "secret").mkdir()
    os.symlink(tmp_path / "secret", wt / "src" / "link")
    assert not tg.scope_gate(ctx(proj, path="src/link/x.py")).allow


@pytest.mark.parametrize("path", ["gates/role.yaml", "workflows/tdd.yaml", "box/policy.dw",
                                  ".aladdin/pins.json", ".git/config", "./gates/../gates/x"])
@pytest.mark.parametrize("tool", ["read_file", "write_file"])
def test_gate_files_are_invisible_to_workers(proj, path, tool):
    v = tg.scope_gate(ctx(proj, tool=tool, path=path))
    assert not v.allow and v.rule == "gate.integrity"


def test_scope_checks_every_path_in_list(proj):
    assert not tg.scope_gate(ctx(proj, tool="list_files", paths=["src", "/tmp"])).allow


def test_scope_denies_non_string_path(proj):
    assert not tg.scope_gate(ctx(proj, path={"x": 1})).allow


# --- role file gate ----------------------------------------------------------
def test_red_may_write_tests_only(proj):
    assert tg.role_files_gate(ctx(proj, role="red", path="tests/test_semver.py")).allow
    v = tg.role_files_gate(ctx(proj, role="red", path="src/semver.py"))
    assert not v.allow and v.rule == "gate.red.tests_only"


@pytest.mark.parametrize("role", ["green", "scaffold"])
def test_green_and_scaffold_may_not_touch_tests(proj, role):
    assert tg.role_files_gate(ctx(proj, role=role, path="src/semver.py")).allow
    assert not tg.role_files_gate(ctx(proj, role=role, path="tests/test_semver.py")).allow
    assert not tg.role_files_gate(ctx(proj, role=role, path="src/conftest.py")).allow


def test_reads_are_not_role_file_gated(proj):
    assert tg.role_files_gate(ctx(proj, role="green", tool="read_file", path="tests/test_x.py")).allow


# --- commit-after-tests (temporal) ---------------------------------------------
def test_commit_without_passing_tests_denied(proj):
    v = tg.commit_gate(ctx(proj, role="host", tool="commit", tree="abc"))
    assert not v.allow and v.rule == "gate.commit_after_tests"


def test_commit_after_fresh_pass_on_same_tree_allowed(proj):
    c = ctx(proj, role="host", tool="commit", tree="abc")
    c.log.append("tests.passed", task="S1-01-T1", tree="abc")
    assert tg.commit_gate(c).allow


def test_commit_denied_when_tree_changed_since_tests(proj):
    c = ctx(proj, role="host", tool="commit", tree="def")
    c.log.append("tests.passed", task="S1-01-T1", tree="abc")
    assert not tg.commit_gate(c).allow


def test_commit_denied_when_pass_older_than_an_hour(proj):
    c = ctx(proj, role="host", tool="commit", tree="abc")
    c.log.append("tests.passed", task="S1-01-T1", tree="abc")
    c.now = time.time() + 3601
    assert not tg.commit_gate(c).allow


def test_commit_denied_when_later_run_failed(proj):
    c = ctx(proj, role="host", tool="commit", tree="abc")
    c.log.append("tests.passed", task="S1-01-T1", tree="abc")
    c.log.append("tests.failed", task="S1-01-T1", tree="abc")
    assert not tg.commit_gate(c).allow


def test_commit_denied_when_log_tampered(proj):
    c = ctx(proj, role="host", tool="commit", tree="abc")
    c.log.append("tests.failed", task="S1-01-T1", tree="abc")
    c.log.path.write_text(c.log.path.read_text().replace("tests.failed", "tests.passed"))
    v = tg.commit_gate(c)
    assert not v.allow and "tamper" in v.reason


# --- fail closed + logging -----------------------------------------------------
def test_crashing_gate_denies(proj):
    def boom(_):
        raise RuntimeError("bug in gate")
    v = base.run_gate(boom, ctx(proj))
    assert not v.allow and v.rule.endswith(".crash")


def test_gate_returning_garbage_denies(proj):
    v = base.run_gate(lambda _: "yes", ctx(proj))
    assert not v.allow


def test_evaluate_logs_every_decision(proj):
    c = ctx(proj, role="green", tool="write_file", path="gates/x")
    v = base.evaluate(tg.TOOL_GATES, c)
    assert not v.allow
    rows = list(c.log.query(kind="gate.decision"))
    assert rows and rows[-1]["verdict"] == "deny" and rows[-1]["rule"] == "gate.integrity"
    assert {"reason", "role", "task", "tool", "commit"} <= set(rows[-1])


def test_unrecordable_decision_is_a_deny(proj):
    c = ctx(proj, role="green", tool="read_file", path="src/a.py")
    c.log.append("x", n=1)
    c.log.path.write_text(c.log.path.read_text().replace('"n": 1', '"n": 2'))
    v = base.evaluate(tg.TOOL_GATES, c)
    assert not v.allow and v.rule == "gate.log_integrity"
    assert (c.log.path.with_name("alarms.jsonl")).exists()


def test_shipped_role_config_matches_code():
    from importlib import resources
    import yaml
    d = yaml.safe_load((resources.files("aloop_strands") / "data" / "gates" / "role.yaml").read_text())
    assert {r: frozenset(t) for r, t in d["roles"].items()} == tg.ROLE_TOOLS
    assert tuple(d["protected_paths"]) == tg.PROTECTED
