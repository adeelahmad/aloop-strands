import json
import subprocess
import sys

from aloop_strands import models


def test_route_by_role(monkeypatch):
    monkeypatch.setenv("ALOOP_MODEL_PLANNING", "bedrock/global.anthropic.claude-opus-4-6-v1")
    monkeypatch.setenv("ALOOP_MODEL_WORKER", "bedrock/global.anthropic.claude-sonnet-4-6")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    assert models.route("planner") == "bedrock/global.anthropic.claude-opus-4-6-v1"
    assert models.route("green") == "bedrock/global.anthropic.claude-sonnet-4-6"
    m = models.make_model("green")
    assert "sonnet" in json.dumps(m.get_config(), default=str)


def test_default_route(monkeypatch):
    for k in ("ALOOP_MODEL", "ALOOP_MODEL_PLANNING", "ALOOP_MODEL_WORKER"):
        monkeypatch.delenv(k, raising=False)
    assert models.route("red").startswith("bedrock/")


def run(*a, cwd=None):
    return subprocess.run([sys.executable, "-m", "aloop_strands.cli", *a], capture_output=True, text=True, cwd=cwd)


def test_cli_approve_wrong_code_blocks(tmp_path):
    run("init", str(tmp_path))
    d = tmp_path / "docs" / "agents" / "sprint1"
    d.mkdir(parents=True)
    (d / "plan.json").write_text("{}")
    r = run("approve", "--root", str(tmp_path), "--sprint", "1", "deadbeef")
    assert r.returncode == 2 and "BLOCK" in r.stdout


def test_cli_status_prints_code(tmp_path):
    run("init", str(tmp_path))
    d = tmp_path / "docs" / "agents" / "sprint1"
    d.mkdir(parents=True)
    (d / "plan.json").write_text("{}")
    r = run("status", "--root", str(tmp_path))
    assert r.returncode == 0 and "code" in r.stdout
