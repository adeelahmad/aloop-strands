"""Stop gates: a worker's "done" is never a result; these decide from the worktree itself."""
import subprocess
import sys

import pytest

from aloop_strands.events import EventLog
from aloop_strands.gates import stop_gates as sg


def git(wt, *a):
    subprocess.run(["git", "-C", str(wt), *a], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"})


def commit(wt, msg="c"):
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", msg, "--allow-empty")
    return subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    wt = tmp_path / "wt"
    (wt / "src").mkdir(parents=True)
    (wt / "tests").mkdir()
    (wt / "tests" / "conftest.py").write_text("import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n")
    git(wt, "init", "-q", "-b", "main")
    base = commit(wt, "base")
    return wt, base


def ctx(repo, role, base, **kw):
    wt, _ = repo
    return sg.StopContext(role=role, task="T1", worktree=wt, base=base, log=EventLog(wt.parent),
                          test_cmd=[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], **kw)


TEST = "from semver import parse\n\ndef test_parse():\n    assert parse('1.2.3') == (1, 2, 3)\n"


# --- RED ------------------------------------------------------------------------
def test_red_passes_when_new_tests_fail(repo):
    wt, base = repo
    (wt / "tests" / "test_semver.py").write_text(TEST)
    assert sg.red_verify(ctx(repo, "red", base)).allow


def test_red_blocks_when_tests_pass(repo):
    wt, base = repo
    (wt / "tests" / "test_trivial.py").write_text("def test_ok():\n    assert True\n")
    v = sg.red_verify(ctx(repo, "red", base))
    assert not v.allow and "pass" in v.reason


def test_red_blocks_production_code(repo):
    wt, base = repo
    (wt / "tests" / "test_semver.py").write_text(TEST)
    (wt / "src" / "semver.py").write_text("def parse(s): return (1, 2, 3)\n")
    v = sg.red_verify(ctx(repo, "red", base))
    assert not v.allow and v.rule == "gate.red.tests_only"


def test_red_blocks_no_new_tests(repo):
    wt, base = repo
    assert not sg.red_verify(ctx(repo, "red", base)).allow


def test_red_blocks_syntax_error_tests(repo):
    wt, base = repo
    (wt / "tests" / "test_bad.py").write_text("def test_x(:\n")
    assert not sg.red_verify(ctx(repo, "red", base)).allow


# --- SCAFFOLD ------------------------------------------------------------------
def _red_done(repo):
    wt, base = repo
    (wt / "tests" / "test_semver.py").write_text(TEST)
    return commit(wt, "red")


def test_scaffold_passes_with_stub(repo):
    wt, _ = repo
    red = _red_done(repo)
    (wt / "src" / "semver.py").write_text("def parse(s):\n    raise NotImplementedError('SUB-AGENT-TODO')\n")
    assert sg.scaffold_verify(ctx(repo, "scaffold", red)).allow


def test_scaffold_blocks_implementation(repo):
    wt, _ = repo
    red = _red_done(repo)
    (wt / "src" / "semver.py").write_text("def parse(s):\n    return tuple(map(int, s.split('.')))\n")
    v = sg.scaffold_verify(ctx(repo, "scaffold", red))
    assert not v.allow and "stub" in v.reason


def test_scaffold_blocks_uncollectable(repo):
    wt, _ = repo
    red = _red_done(repo)
    v = sg.scaffold_verify(ctx(repo, "scaffold", red))
    assert not v.allow


# --- GREEN ---------------------------------------------------------------------
def _scaffold_done(repo):
    wt, _ = repo
    red = _red_done(repo)
    (wt / "src" / "semver.py").write_text("def parse(s):\n    raise NotImplementedError('SUB-AGENT-TODO')\n")
    return red, commit(wt, "scaffold")


IMPL = "def parse(s):\n    return tuple(int(x) for x in s.split('.'))\n"


def test_green_passes(repo):
    wt, _ = repo
    red, sc = _scaffold_done(repo)
    (wt / "src" / "semver.py").write_text(IMPL)
    assert sg.green_verify(ctx(repo, "green", sc, red_commit=red)).allow


def test_green_blocks_failing_tests(repo):
    wt, _ = repo
    red, sc = _scaffold_done(repo)
    v = sg.green_verify(ctx(repo, "green", sc, red_commit=red))
    assert not v.allow and v.rule == "gate.green.tests_pass"


def test_green_blocks_weakened_test(repo):
    wt, _ = repo
    red, sc = _scaffold_done(repo)
    (wt / "src" / "semver.py").write_text(IMPL)
    (wt / "tests" / "test_semver.py").write_text(TEST.replace("(1, 2, 3)", "parse('1.2.3')"))
    v = sg.green_verify(ctx(repo, "green", sc, red_commit=red))
    assert not v.allow and v.rule == "gate.green.tests_unchanged"


def test_green_blocks_new_conftest_monkeypatch(repo):
    wt, _ = repo
    red, sc = _scaffold_done(repo)
    (wt / "src" / "semver.py").write_text(IMPL)
    (wt / "src" / "conftest.py").write_text("")
    assert not sg.green_verify(ctx(repo, "green", sc, red_commit=red)).allow


@pytest.mark.parametrize("marker", ["# pragma: no cover", "# type: ignore", "import pytest\npytest.skip('x')"])
def test_green_blocks_suppressions(repo, marker):
    wt, _ = repo
    red, sc = _scaffold_done(repo)
    (wt / "src" / "semver.py").write_text(IMPL + marker + "\n")
    v = sg.green_verify(ctx(repo, "green", sc, red_commit=red))
    assert not v.allow and v.rule == "gate.green.no_suppressions"


def test_green_blocks_gate_file_change(repo):
    wt, _ = repo
    red, sc = _scaffold_done(repo)
    (wt / "src" / "semver.py").write_text(IMPL)
    (wt / "gates").mkdir()
    (wt / "gates" / "x.yaml").write_text("allow\n")
    v = sg.green_verify(ctx(repo, "green", sc, red_commit=red))
    assert not v.allow and v.rule == "gate.integrity"


# --- structural ---------------------------------------------------------------
def test_structural_flags_orphan_module(repo):
    wt, _ = repo
    red, sc = _scaffold_done(repo)
    (wt / "src" / "semver.py").write_text(IMPL)
    (wt / "src" / "helpers_unused.py").write_text("def f():\n    return 1\n")
    v = sg.structural_verify(ctx(repo, "structural_review", red))
    assert not v.allow and "helpers_unused" in v.reason


def test_structural_flags_duplicate_function(repo):
    wt, _ = repo
    red, sc = _scaffold_done(repo)
    (wt / "src" / "semver.py").write_text(IMPL + "from util import parse as _p\n")
    (wt / "src" / "util.py").write_text(IMPL)
    v = sg.structural_verify(ctx(repo, "structural_review", red))
    assert not v.allow and "parse" in v.reason


def test_structural_passes_clean(repo):
    wt, _ = repo
    red, sc = _scaffold_done(repo)
    (wt / "src" / "semver.py").write_text(IMPL)
    assert sg.structural_verify(ctx(repo, "structural_review", red)).allow


# --- final ---------------------------------------------------------------------
def test_final_requires_full_suite_green(repo):
    wt, base = repo
    red, sc = _scaffold_done(repo)
    assert not sg.final_verify(ctx(repo, "final_verification", base)).allow
    (wt / "src" / "semver.py").write_text(IMPL)
    assert sg.final_verify(ctx(repo, "final_verification", base)).allow


def test_final_blocks_when_no_tests(repo):
    wt, base = repo
    assert not sg.final_verify(ctx(repo, "final_verification", base)).allow


def test_runner_crash_blocks(repo):
    wt, base = repo
    c = ctx(repo, "final_verification", base)
    c.test_cmd = ["/nonexistent/binary"]
    from aloop_strands.gates.base import run_gate
    v = run_gate(sg.final_verify, c)
    assert not v.allow


def test_red_must_fail_reason_names_the_passing_tests(repo):
    # Live run: RED retried 3x blind; the retry must see which tests already pass.
    wt, base = repo
    (wt / "tests" / "test_trivial.py").write_text("def test_empty_is_rejected():\n    assert True\n")
    v = sg.red_verify(ctx(repo, "red", base))
    assert v.rule == "gate.red.must_fail" and "test_empty_is_rejected" in v.reason
    assert "already" in v.reason
