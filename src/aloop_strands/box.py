"""Strands Box integration: every piece of model-written code runs inside a box.

Box is a standalone CLI (https://strandsagents.com/docs/user-guide/box/how-box-works/). We render a
config and a default-deny Dogwood policy per task (box/policy.dw.tmpl, pinned) and run either
- a test command (no network at all), or
- a worker process (egress to the model endpoint only; the key is a phantom placeholder swapped by
  the Box gateway: https://strandsagents.com/docs/user-guide/box/guides/network-and-credentials/).
Commands run through the box's shell (`sh` inside the box), so the policy decides each one, including
the temporal `test_before_commit` rule.

Linux notes learned in the spike: box_dir must be 0700; `write` needs a covering `read`; no `list`.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
from importlib import resources
from pathlib import Path

SENTINEL = "ALOOP-BOX-WORKLOAD-STARTED"
# The sentinel proves the workload started; without it, Box itself failed and there is no result.
WRAP = ("import subprocess,sys; print('" + SENTINEL + "', file=sys.stderr, flush=True); "
        "sys.exit(subprocess.run(['sh','-c',sys.argv[1]]).returncode)")
PROTECTED = ("gates", "workflows", "box", ".aladdin")


BOX_REV = "8c26868a952e954ba1b99b7d7bf46b67da94f4c5"  # pinned Box source revision (Dockerfile, CI)


def find_box() -> str | None:
    return os.environ.get("ALOOP_BOX_BIN") or shutil.which("box")


def check_box(box_bin: str) -> None:
    """aloop-strands drives Box with `box run --config <file>` (Box main at BOX_REV). Released CLIs
    that only take profiles cannot run our per-task configs: refuse them rather than half-work."""
    r = subprocess.run([box_bin, "run", "--help"], capture_output=True, text=True, timeout=30)
    if "--config" not in (r.stdout + r.stderr):
        raise BoxError(f"{box_bin} has no `run --config`; build Box at {BOX_REV} (see docs/DEPLOY.md)")


def _q(s: str) -> str:
    return json.dumps(str(s))


def _arr(xs) -> str:
    return "[" + ", ".join(_q(x) for x in xs) + "]"


def python_grants() -> tuple[list[str], str, str]:
    """(read paths, real interpreter, venv python) for the running interpreter."""
    stdlib = sysconfig.get_paths()["stdlib"]
    reads = [stdlib, sys.prefix, os.path.realpath(sys.prefix)]
    lib = sysconfig.get_config_var("LDLIBRARY")
    libdir = sysconfig.get_config_var("LIBDIR")
    if lib and libdir and os.path.exists(os.path.join(libdir, lib)):
        reads.append(os.path.join(libdir, lib))
    pkg = os.path.realpath(Path(__file__).parent)
    reads.append(str(Path(pkg).parent))  # aloop_strands package (editable or site-packages)
    real = os.path.realpath(sys.executable)
    reads.append(str(Path(real).parent))
    venv_bin = Path(sys.prefix) / "bin"
    venv_py = str(venv_bin) if (venv_bin / "python").exists() else str(Path(real).parent)
    return _outermost(reads), real, venv_py


def _outermost(paths: list[str]) -> list[str]:
    """Drop duplicates and paths inside another listed path: Box refuses nested same-kind grants."""
    norm = [p.rstrip("/") or "/" for p in paths]
    out = []
    for p in norm:
        inside = any(q != p and (p + "/").startswith(q.rstrip("/") + "/") for q in norm)
        if not inside and p not in out:
            out.append(p)
    return out


def policy_template(project_root: Path | None = None) -> str:
    if project_root and (Path(project_root) / "box" / "policy.dw.tmpl").exists():
        return (Path(project_root) / "box" / "policy.dw.tmpl").read_text()
    return (resources.files("aloop_strands") / "data" / "box" / "policy.dw.tmpl").read_text()


def render(*, worktree: Path, state_dir: Path, argv: list[str], extra_rw: list[str] = (),
           model_host: str | None = None, secret_env: str | None = None, env: dict | None = None,
           project_root: Path | None = None) -> tuple[str, str]:
    wt = os.path.realpath(worktree)
    reads, real, venv_py = python_grants()
    tmp = str(Path(state_dir).parent / (Path(state_dir).name + "-tmp"))
    rw = [wt, tmp, *extra_rw]
    egress = ""
    if model_host:
        egress = (f'\n@id("model_connect")\npermit (principal, action == Box::Action::"net:connect", resource)\n'
                  f'when {{ context.input.host == {_q(model_host)} && context.input.port == 443 }};\n'
                  f'@id("model_request")\npermit (principal, action == Box::Action::"http:request", resource)\n'
                  f'when {{ context.input.host == {_q(model_host)} }};\n')
    policy = (policy_template(project_root).replace("{{WORKTREE}}", wt).replace("{{EGRESS_RULES}}", egress))
    base_env = {"PATH": "/usr/bin:/bin", "TMPDIR": tmp, "HOME": tmp, "PYTHONDONTWRITEBYTECODE": "1",
                "GIT_AUTHOR_NAME": "aloop-worker", "GIT_AUTHOR_EMAIL": "worker@aloop.invalid",
                "GIT_COMMITTER_NAME": "aloop-worker", "GIT_COMMITTER_EMAIL": "worker@aloop.invalid",
                **(env or {})}
    env_s = "{ " + ", ".join(f"{k} = {_q(v)}" for k, v in base_env.items()) + " }"
    # The Linux backend can only deny paths that exist; a new one is caught by the policy and the host stop gates.
    deny = [os.path.join(wt, p) for p in PROTECTED if os.path.exists(os.path.join(wt, p))]
    toml = f"""# Rendered by aloop-strands for one task. Docs: https://strandsagents.com/docs/user-guide/box/reference/configuration/
name = {_q('aloop-' + hashlib.sha256(wt.encode()).hexdigest()[:10])}
box_dir = {_q(state_dir)}
policy = "policy.dw"
monty = false

[agent]
command = {_arr([real, '-c', WRAP, shlex.join(argv)])}
workspace = {_q(wt)}
env = {env_s}

[agent.filesystem]
exec = {_arr(_outermost([os.path.realpath(p) for p in (real, *reads)]))}
read = {_arr(_outermost(reads + rw))}
write = {_arr(_outermost(rw))}
deny = {_arr(deny)}
"""
    # A tool answers the name its command file has, so each name points at its own file.
    for name in ("python", "python3"):
        toml += f"""
[tool.{name}]
command = {_arr([os.path.join(venv_py, name)])}
env = {env_s}
[tool.{name}.filesystem]
read = {_arr(_outermost(reads + rw))}
write = {_arr(_outermost(rw))}
deny = {_arr(deny)}
"""
    toml += f"""
[tool.git]
command = ["/usr/bin/git"]
env = {env_s}
[tool.git.filesystem]
read = {_arr([wt, tmp, '/usr/share/git-core', '/usr/lib/git-core', *extra_rw])}
write = {_arr(_outermost(rw))}
deny = {_arr(deny)}
"""
    if model_host:
        toml += f"""
[egress.model]
destinations = [{_q(model_host)}]
secret.ref = {_q('env://' + (secret_env or 'ALOOP_MODEL_KEY'))}
"""
    return toml, policy


class BoxError(RuntimeError):
    """Box itself failed (bad config or policy, sandbox setup). Never read as a test result."""


class BoxExecutor:
    """Runs a command inside a box for one worktree. The box state dir is kept per worktree so the
    temporal rules see the worktree's history."""

    def __init__(self, box_bin: str, state_root: Path | None = None, project_root: Path | None = None, log=None):
        check_box(box_bin)
        # Box's broker socket lives in box_dir and Unix socket paths are short (~108 bytes): keep it near /.
        state_root = state_root or Path(os.environ.get("ALOOP_BOX_STATE", "/tmp/aloop-box"))
        self.box_bin, self.state_root, self.project_root, self.log = box_bin, Path(state_root), project_root, log

    def state_dir(self, cwd: Path) -> Path:
        d = self.state_root / hashlib.sha256(os.path.realpath(cwd).encode()).hexdigest()[:16]
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        Path(str(d) + "-tmp").mkdir(exist_ok=True)
        return d

    def run(self, argv: list[str], cwd: Path, timeout: int, **kw) -> subprocess.CompletedProcess:
        sd = self.state_dir(cwd)
        toml, policy = render(worktree=cwd, state_dir=sd, argv=argv, project_root=self.project_root, **kw)
        cdir = Path(str(sd) + "-cfg")
        cdir.mkdir(exist_ok=True)
        cfg = cdir / "box.toml"
        cfg.write_text(toml)
        (cdir / "policy.dw").write_text(policy)
        r = subprocess.run([self.box_bin, "run", "--config", str(cfg)], capture_output=True, text=True,
                           timeout=timeout, cwd=cdir)
        fatal = [ln for ln in r.stderr.splitlines() if ln.startswith("strands-box: error")]
        if fatal or SENTINEL not in r.stderr:
            detail = fatal[0] if fatal else "\n".join(r.stderr.splitlines()[-15:])
            if self.log is not None:
                self.log.append("box.error", argv=argv, error=detail[:2000])
            raise BoxError(f"box did not run the workload (exit {r.returncode}): {detail}")
        if self.log is not None:
            denials = [ln for ln in r.stderr.splitlines() if "policy denied" in ln or "[default-deny]" in ln]
            self.log.append("box.run", argv=argv, exit=r.returncode, denials=denials[:20],
                            worktree=os.path.realpath(cwd))
        err = "\n".join(ln for ln in r.stderr.splitlines() if not ln.startswith("strands-box:")
                        and not ln.startswith("  ") and ln != SENTINEL)
        return subprocess.CompletedProcess(argv, r.returncode, r.stdout, err)

    __call__ = lambda self, cmd, cwd, timeout: self.run(list(cmd), Path(cwd), timeout)  # noqa: E731
