"""Box layer (OS sandbox + Dogwood policy). Skipped when no `box` binary (set ALOOP_BOX_BIN)."""
import os
import subprocess
import sys

import pytest

from aloop_strands import box
from aloop_strands.events import EventLog

BOX = box.find_box()
needs_box = pytest.mark.skipif(not BOX, reason="no box binary (ALOOP_BOX_BIN)")


def test_render_is_default_deny_and_project_scoped(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    toml, policy = box.render(worktree=wt, state_dir=tmp_path / "s", argv=["python", "-m", "pytest"])
    assert "net:connect" not in policy  # no egress at all for test boxes
    assert str(wt.resolve()) in policy and "{{" not in policy
    assert "test_before_commit" in policy and "gate_files_hidden" in policy
    assert 'monty = false' in toml and "deny = " in toml


def test_worker_render_allows_only_model_host(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    toml, policy = box.render(worktree=wt, state_dir=tmp_path / "s", argv=["x"],
                              model_host="bedrock-runtime.us-west-2.amazonaws.com", secret_env="AWS_BEARER_TOKEN_BEDROCK")
    assert policy.count("net:connect") == 1 and "bedrock-runtime.us-west-2.amazonaws.com" in policy
    assert 'secret.ref = "env://AWS_BEARER_TOKEN_BEDROCK"' in toml


def test_policy_template_is_pinned_data():
    from importlib import resources
    assert (resources.files("aloop_strands") / "data" / "box" / "policy.dw.tmpl").is_file()


@pytest.fixture
def wt(tmp_path):
    w = tmp_path / "wt"
    (w / "gates").mkdir(parents=True)
    (w / "gates" / "role.yaml").write_text("deny: write\n")
    (w / "test_a.py").write_text("def test_a():\n    assert True\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=w, check=True)
    subprocess.run(["git", "add", "-A"], cwd=w, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=w, check=True, env=env)
    return w


def ex(tmp_path, wt):
    return box.BoxExecutor(BOX, tmp_path / "boxes", log=EventLog(tmp_path))


@needs_box
def test_tests_run_in_box_and_exit_codes_propagate(tmp_path, wt):
    e = ex(tmp_path, wt)
    assert e([sys.executable and "python", "-m", "pytest", "-q", "-p", "no:cacheprovider"], wt, 120).returncode == 0
    (wt / "test_b.py").write_text("def test_b():\n    assert False\n")
    assert e(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"], wt, 120).returncode != 0


@needs_box
def test_box_blocks_gate_file_write(tmp_path, wt):
    r = ex(tmp_path, wt)(["sh", "-c", "echo 'allow: all' > gates/role.yaml"], wt, 60)
    assert r.returncode != 0
    assert (wt / "gates" / "role.yaml").read_text() == "deny: write\n"


@needs_box
def test_box_blocks_write_outside_worktree(tmp_path, wt):
    target = tmp_path / "escaped.txt"
    ex(tmp_path, wt)(["python", "-c", f"open({str(target)!r}, 'w').write('x')"], wt, 60)
    assert not target.exists()


@needs_box
def test_box_blocks_network(tmp_path, wt):
    r = ex(tmp_path, wt)(["python", "-c", "import urllib.request as u; u.urlopen('https://example.com', timeout=10)"], wt, 60)
    assert r.returncode != 0


@needs_box
def test_box_commit_needs_passing_tests_first(tmp_path, wt):
    e = ex(tmp_path, wt)
    (wt / "f.txt").write_text("x")
    r = e(["sh", "-c", "git add f.txt && git commit -qm nope"], wt, 60)
    assert r.returncode != 0
    log = list(EventLog(tmp_path).query(kind="box.run"))
    assert any("test_before_commit" in d for d in log[-1]["denials"])
    assert e(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"], wt, 120).returncode == 0
    r = e(["sh", "-c", "git add f.txt && git commit -qm yes"], wt, 60)
    assert r.returncode == 0, r.stderr


@needs_box
def test_box_config_error_raises_not_a_result(tmp_path, wt):
    root = tmp_path / "proj"
    (root / "box").mkdir(parents=True)
    (root / "box" / "policy.dw.tmpl").write_text("permit (principal, action, resource) when { 1 || };\n")
    e = box.BoxExecutor(BOX, tmp_path / "boxes", project_root=root)
    with pytest.raises(box.BoxError):
        e(["python", "-m", "pytest"], wt, 60)


def test_read_grants_never_nest():
    # Box refuses a grant inside another grant of the same kind (CI: stdlib under sys.prefix).
    from aloop_strands.box import _outermost
    got = _outermost(["/opt/py/lib/python3.13", "/opt/py", "/opt/py/bin", "/srv/x", "/opt/pyx"])
    assert got == ["/opt/py", "/srv/x", "/opt/pyx"]
    from pathlib import Path
    toml, _ = box.render(worktree=Path("/tmp/wt"), state_dir=Path("/tmp/st"), argv=["python"])
    import tomllib
    reads = tomllib.loads(toml)["agent"]["filesystem"]["read"]
    assert all(not (a != b and (a + "/").startswith(b.rstrip("/") + "/")) for a in reads for b in reads)


def test_exec_covers_python_extension_modules():
    # CI: lib-dynload .so failed to map (no exec grant) when Python lives outside /usr.
    import sysconfig, tomllib
    from pathlib import Path
    toml, _ = box.render(worktree=Path("/tmp/wt"), state_dir=Path("/tmp/st"), argv=["python"])
    ex = tomllib.loads(toml)["agent"]["filesystem"]["exec"]
    dyn = os.path.realpath(sysconfig.get_paths()["platstdlib"]) + "/lib-dynload/x.so"
    assert any(dyn.startswith(e.rstrip("/") + "/") for e in ex), ex
    assert "/tmp/wt" not in ex
