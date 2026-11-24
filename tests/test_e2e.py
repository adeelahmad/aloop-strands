"""End to end: intent -> approved plan -> RED -> SCAFFOLD -> GREEN -> review -> merged on main,
no human input after approval; plus the negative cases (gate edit, out-of-worktree write,
commit without passing tests) blocked and logged."""
import subprocess

import pytest

from aloop_strands import approvals, project, supervisor
from aloop_strands.events import EventLog

import scripted_semver as S


def git(root, *a):
    return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "app"
    project.init(root)
    return root


def run_to_approval(root, models):
    sup = supervisor.Supervisor(root, models=models)
    st = sup.plan("a semver parser")
    assert st.status == "awaiting_approval", st.reason
    approvals.approve(root, 1, st.code, actor="human:test")
    return sup


def test_semver_intent_reaches_main(proj):
    sup = run_to_approval(proj, S.models())
    before = git(proj, "rev-parse", "main").strip()
    result = sup.execute()
    assert result.status == "done", result.reason
    assert git(proj, "rev-parse", "main").strip() != before
    assert "def parse" in git(proj, "show", "main:src/semver.py")
    assert "test_parses_three_numbers" in git(proj, "show", "main:tests/test_semver.py")
    # main's suite is green, run fresh
    r = subprocess.run(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=proj, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    log = EventLog(proj)
    assert log.verify() == (True, None)
    rules = [e["rule"] for e in log.query(kind="gate.decision") if e["verdict"] == "allow" and e["tool"] == "<stop>"]
    for rule in ("gate.red", "gate.scaffold", "gate.green", "gate.structural", "gate.final"):
        assert rule in rules
    merges = list(log.query(kind="merge"))
    assert merges and merges[-1]["task"] == "S1-01-T1" and merges[-1]["commit"]
    # every merged commit traces to its task
    assert "S1-01-T1" in git(proj, "log", "--format=%B", "main")


def test_no_human_input_after_approval(proj, monkeypatch):
    sup = run_to_approval(proj, S.models())
    import builtins
    monkeypatch.setattr(builtins, "input", lambda *a: (_ for _ in ()).throw(AssertionError("asked a human")))
    assert sup.execute().status == "done"


def test_execute_refuses_without_approval(proj):
    sup = supervisor.Supervisor(proj, models=S.models())
    st = sup.plan("a semver parser")
    r = sup.execute()
    assert r.status == "blocked" and "approval" in r.reason
    assert not (proj / "src" / "semver.py").exists()


def test_negative_worker_edits_gate_and_escapes_worktree(proj, tmp_path):
    outside = tmp_path / "pwned.py"
    green = [S.w("write_file", "gates/role.yaml", "allow: everything\n"),
             S.w("write_file", str(outside), "x"),
             S.w("write_file", "../../workflows/tdd.yaml", "steps: []\n"),
             S.w("write_file", "src/semver.py", S.IMPL), "done"]
    sup = run_to_approval(proj, S.models(green_turns=green))
    assert sup.execute().status == "done"
    assert not outside.exists()
    assert "allow: everything" not in (proj / "gates" / "role.yaml").read_text()
    denied = [e for e in EventLog(proj).query(kind="gate.decision") if e["verdict"] == "deny"]
    rules = {e["rule"] for e in denied}
    assert {"gate.integrity", "gate.scope"} <= rules


def test_negative_commit_without_passing_tests_is_blocked(proj):
    broken = "def parse(text):\n    return (0, 0, 0)\n"
    green = [S.w("write_file", "src/semver.py", broken), "done, trust me"]
    sup = run_to_approval(proj, S.models(green_turns=green))
    r = sup.execute(max_attempts=2)
    assert r.status == "blocked"
    assert "src/semver.py" not in git(proj, "ls-tree", "-r", "--name-only", "main")
    assert not list(EventLog(proj).query(kind="merge"))
    denied = [e for e in EventLog(proj).query(kind="gate.decision") if e["verdict"] == "deny"]
    assert any(e["rule"] == "gate.green.tests_pass" for e in denied)
    assert any(e["rule"] == "gate.commit_after_tests" for e in denied)


def test_negative_worker_weakens_test(proj):
    weak = S.TESTS.replace('assert parse("1.2.3") == (1, 2, 3)', 'assert True')
    green = [S.w("write_file", "tests/test_semver.py", weak), S.w("write_file", "src/semver.py", S.IMPL), "done"]
    sup = run_to_approval(proj, S.models(green_turns=green))
    assert sup.execute().status == "done"  # the edit was denied at the tool gate, GREEN still passed honestly
    assert 'assert parse("1.2.3") == (1, 2, 3)' in git(proj, "show", "main:tests/test_semver.py")


# --- inside Box ---------------------------------------------------------------------
from aloop_strands import box as _box  # noqa: E402

needs_box = pytest.mark.skipif(not _box.find_box(), reason="no box binary (ALOOP_BOX_BIN)")


def run_to_approval_box(root, models):
    sup = supervisor.Supervisor(root, models=models, box_bin=_box.find_box())
    st = sup.plan("a semver parser")
    approvals.approve(root, 1, st.code, actor="human:test")
    return sup


@needs_box
def test_semver_in_box_reaches_main(proj):
    sup = run_to_approval_box(proj, S.models())
    r = sup.execute()
    assert r.status == "done", r.reason
    assert "def parse" in git(proj, "show", "main:src/semver.py")
    log = EventLog(proj)
    assert log.verify() == (True, None)
    exits = list(log.query(kind="worker.exit"))
    assert exits and all(e["box"] and e["exit"] == 0 for e in exits)
    roles_in_box = {e["role"] for e in exits}
    assert {"red", "scaffold", "green", "structural_review", "final_verification"} <= roles_in_box
    assert list(log.query(kind="box.run"))  # tests ran in boxes too


@needs_box
def test_negative_in_box_gate_edit_and_escape_blocked(proj, tmp_path):
    outside = tmp_path / "pwned.py"
    green = [S.w("write_file", "gates/role.yaml", "allow: everything\n"),
             S.w("write_file", str(outside), "x"),
             S.w("write_file", "src/semver.py", S.IMPL), "done"]
    sup = run_to_approval_box(proj, S.models(green_turns=green))
    assert sup.execute().status == "done"
    assert not outside.exists()
    assert "allow: everything" not in git(proj, "show", "main:gates/role.yaml")
    ex = [e for e in EventLog(proj).query(kind="worker.exit") if e["role"] == "green"]
    assert ex[-1]["reported_denials"] >= 2
