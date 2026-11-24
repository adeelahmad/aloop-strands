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


def test_cli_run_refuses_without_box(tmp_path, monkeypatch):
    run("init", str(tmp_path))
    import os
    env = {k: v for k, v in os.environ.items() if k != "ALOOP_BOX_BIN"}
    env["PATH"] = "/usr/bin:/bin"
    r = subprocess.run([sys.executable, "-m", "aloop_strands.cli", "run", "--root", str(tmp_path)],
                       capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "Box" in (r.stdout + r.stderr)


def test_worker_env_passes_model_config_but_never_keys(monkeypatch):
    from aloop_strands import models
    monkeypatch.setenv("OPENAI_BASE_URL", "https://lm.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("ALOOP_MODEL", "openai/qwen")
    env = models.worker_env()
    assert env["OPENAI_BASE_URL"] == "https://lm.example/v1" and env["ALOOP_MODEL"] == "openai/qwen"
    assert not any("KEY" in k or "TOKEN" in k or "SECRET" in k for k in env)
