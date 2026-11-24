"""Worker tools, bound to one worktree. Strands @tool: https://strandsagents.com/docs/user-guide/sdk/tools/custom-tools/

Gates run before every call (gates/intervention.py); the tools resolve paths again as defence
in depth. `run_tests` executes model-written code, so in Box mode it runs inside the box
(executor injected); results are recorded by this host-side tool, never by the model.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Callable

from strands import tool

from .events import EventLog
from .worktree import tree_hash

Executor = Callable[[list[str], Path, int], subprocess.CompletedProcess]


def local_executor(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _inside(wt: Path, path: str) -> Path:
    full = Path(os.path.realpath(wt / path))
    full.relative_to(os.path.realpath(wt))  # raises ValueError outside
    return full


def make_tools(role_tools: frozenset[str], worktree: Path, project_root: Path, task: str,
               test_cmd: str = "python -m pytest -q", executor: Executor = local_executor) -> list:
    wt = Path(worktree)
    log = EventLog(project_root)

    @tool
    def read_file(path: str) -> str:
        """Read a text file in the task worktree."""
        return _inside(wt, path).read_text()

    @tool
    def list_files(path: str = ".") -> str:
        """List files under a directory of the task worktree."""
        base = _inside(wt, path)
        return "\n".join(sorted(p.relative_to(wt).as_posix() for p in base.rglob("*")
                                if p.is_file() and ".git" not in p.parts))

    @tool
    def write_file(path: str, content: str) -> str:
        """Create or overwrite a file in the task worktree."""
        f = _inside(wt, path)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        return f"wrote {path}"

    @tool
    def edit_file(path: str, old: str, new: str) -> str:
        """Replace exactly one occurrence of `old` with `new` in a file."""
        f = _inside(wt, path)
        text = f.read_text()
        if text.count(old) != 1:
            return f"error: expected exactly one match, found {text.count(old)}"
        f.write_text(text.replace(old, new))
        return f"edited {path}"

    @tool
    def run_tests() -> str:
        """Run the project's test command in the worktree and return its output."""
        tree = tree_hash(wt)
        try:
            r = executor(shlex.split(test_cmd), wt, 600)
            code, out = r.returncode, (r.stdout + r.stderr)[-4000:]
        except subprocess.TimeoutExpired:
            code, out = 124, "timeout"
        log.append("tests.passed" if code == 0 else "tests.failed", task=task, tree=tree, exit=code)
        return f"exit {code}\n{out}"

    @tool
    def write_artifact(path: str, content: str) -> str:
        """Write a planning artifact under docs/agents/."""
        f = _inside(wt, path)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        return f"wrote {path}"

    write_report = write_artifact
    available = {"read_file": read_file, "list_files": list_files, "write_file": write_file,
                 "edit_file": edit_file, "run_tests": run_tests, "write_artifact": write_artifact}
    out = [available[n] for n in sorted(role_tools) if n in available]
    if "write_report" in role_tools:
        out.append(write_report)
    return out
