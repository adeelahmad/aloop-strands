import subprocess
import sys


def run(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "aloop_strands.cli", *args], capture_output=True, text=True, cwd=cwd)


def test_version():
    r = run("--version")
    assert r.returncode == 0 and "aloop-strands" in r.stdout


def test_init_creates_pins_and_log(tmp_path):
    r = run("init", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / ".aladdin" / "pins.json").exists()
    assert (tmp_path / "workflows" / "tdd.yaml").exists()
    assert run("verify", str(tmp_path)).returncode == 0


def test_verify_fails_on_tamper(tmp_path):
    run("init", str(tmp_path))
    (tmp_path / "workflows" / "tdd.yaml").write_text("steps: []\n")
    r = run("verify", str(tmp_path))
    assert r.returncode == 2 and "workflows/tdd.yaml" in r.stdout
