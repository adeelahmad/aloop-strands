"""Stop gates: run on the host after a worker returns. They read the worktree and run the tests
themselves; nothing the worker said counts. A crash in any of them is a deny (base.run_gate)."""
from __future__ import annotations

import ast
import py_compile
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..events import EventLog
from ..tools import local_executor
from ..worktree import tree_hash
from .base import Verdict
from .tool_gates import PROTECTED, is_test_path

SKIP_PARTS = {"__pycache__", ".pytest_cache", ".aladdin"}
SUPPRESSIONS = re.compile(r"pragma:\s*no cover|type:\s*ignore|noqa|pytest\.skip|pytest\.mark\.(skip|xfail)"
                          r"|unittest\.skip|pytest\.importorskip")


@dataclass
class StopContext:
    role: str
    task: str
    worktree: Path
    base: str                       # commit this step started from
    log: EventLog
    test_cmd: list[str] = field(default_factory=lambda: ["python", "-m", "pytest", "-q"])
    red_commit: str | None = None   # GREEN: tests must equal RED's
    executor: Callable = local_executor


def _git(wt: Path, *a: str) -> str:
    r = subprocess.run(["git", "-C", str(wt), *a], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a)} failed: {r.stderr.strip()}")
    return r.stdout


def changed(wt: Path, since: str) -> list[str]:
    files = set(_git(wt, "diff", "--name-only", since, "--").split())
    files |= set(_git(wt, "ls-files", "--others", "--exclude-standard").split())
    return sorted(f for f in files if not SKIP_PARTS & set(Path(f).parts))


def _protected(files: list[str]) -> Verdict | None:
    bad = [f for f in files if Path(f).parts[0] in PROTECTED]
    return Verdict.deny("gate.integrity", f"worker changed gate/run files: {bad}") if bad else None


def _run(ctx: StopContext, extra: list[str] = ()) -> tuple[int, str]:
    r = ctx.executor([*ctx.test_cmd, *extra], ctx.worktree, 600)
    if not extra:
        ctx.log.append("tests.passed" if r.returncode == 0 else "tests.failed", task=ctx.task,
                       tree=tree_hash(ctx.worktree), exit=r.returncode, by="host")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def red_verify(ctx: StopContext) -> Verdict:
    files = changed(ctx.worktree, ctx.base)
    if (v := _protected(files)):
        return v
    prod = [f for f in files if not is_test_path(f)]
    if prod:
        return Verdict.deny("gate.red.tests_only", f"RED changed production files: {prod}")
    tests = [f for f in files if f.endswith(".py") and Path(f).name.startswith("test_")]
    if not tests:
        return Verdict.deny("gate.red.has_tests", "RED added no test file")
    for t in tests:
        try:
            py_compile.compile(str(ctx.worktree / t), doraise=True)
        except py_compile.PyCompileError as e:
            return Verdict.deny("gate.red.syntax", f"{t} does not compile: {e.msg}")
    code, out = _run(ctx)
    if code == 0:
        _, listing = _run(ctx, ["-rA", "-q"])
        passed = [ln.split(" ", 1)[1] for ln in listing.splitlines() if ln.startswith("PASSED ")][:20]
        return Verdict.deny("gate.red.must_fail", "RED tests pass at first run; they prove nothing. "
                            f"These already pass on the current code: {passed or listing[-400:]}. "
                            "Test behaviour the code does not have yet.")
    if code == 5:
        return Verdict.deny("gate.red.has_tests", "no tests were collected")
    return Verdict.ok("gate.red", f"tests fail as required (exit {code})")


def _is_stub(fn: ast.AST) -> bool:
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Raise) or body[0].exc is None:
        return False
    exc = body[0].exc
    name = exc.func if isinstance(exc, ast.Call) else exc
    return isinstance(name, ast.Name) and name.id == "NotImplementedError"


def scaffold_verify(ctx: StopContext) -> Verdict:
    files = changed(ctx.worktree, ctx.base)
    if (v := _protected(files)):
        return v
    tests = [f for f in files if is_test_path(f)]
    if tests:
        return Verdict.deny("gate.scaffold.no_tests", f"SCAFFOLD changed tests: {tests}")
    for f in files:
        if not f.endswith(".py"):
            continue
        tree = ast.parse((ctx.worktree / f).read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not _is_stub(node):
                return Verdict.deny("gate.scaffold.stubs_only",
                                    f"{f}:{node.name} is not a stub (body must raise NotImplementedError)")
    code, out = _run(ctx, ["--collect-only"])
    if code != 0:
        return Verdict.deny("gate.scaffold.collectable", f"tests are not collectable after SCAFFOLD:\n{out[-800:]}")
    code, _ = _run(ctx)
    if code == 0:
        return Verdict.deny("gate.scaffold.still_red", "tests pass after SCAFFOLD; stubs must not implement")
    return Verdict.ok("gate.scaffold")


def _test_files_at(wt: Path, commit: str) -> set[str]:
    return {f for f in _git(wt, "ls-tree", "-r", "--name-only", commit).split() if is_test_path(f)}


def green_verify(ctx: StopContext) -> Verdict:
    files = changed(ctx.worktree, ctx.base)
    if (v := _protected(files)):
        return v
    red = ctx.red_commit or ctx.base
    moved = [f for f in changed(ctx.worktree, red) if is_test_path(f)]
    now_tests = {f for f in _git(ctx.worktree, "ls-files", "--cached", "--others", "--exclude-standard").split()
                 if is_test_path(f) and not SKIP_PARTS & set(Path(f).parts) and (ctx.worktree / f).exists()}
    if moved or now_tests != _test_files_at(ctx.worktree, red):
        return Verdict.deny("gate.green.tests_unchanged",
                            f"tests differ from RED's: {sorted(set(moved) | (now_tests ^ _test_files_at(ctx.worktree, red)))}")
    for f in files:
        p = ctx.worktree / f
        if p.is_file() and SUPPRESSIONS.search(p.read_text(errors="replace")):
            return Verdict.deny("gate.green.no_suppressions", f"{f} contains a suppression")
    code, out = _run(ctx)
    if code != 0:
        return Verdict.deny("gate.green.tests_pass", f"tests fail (exit {code}):\n{out[-800:]}")
    return Verdict.ok("gate.green")


def _modules(wt: Path) -> list[Path]:
    return [p for p in wt.rglob("*.py") if not SKIP_PARTS & set(p.parts) and ".git" not in p.parts]


def structural_verify(ctx: StopContext) -> Verdict:
    files = [f for f in changed(ctx.worktree, ctx.base) if f.endswith(".py") and not is_test_path(f)]
    every = _modules(ctx.worktree)
    sources = {p: p.read_text(errors="replace") for p in every}
    orphans = []
    for f in files:
        mod = Path(f).stem
        if mod == "__init__":
            continue
        pat = re.compile(rf"^\s*(from\s+[\w.]*\b{mod}\b|import\s+[\w., ]*\b{mod}\b)", re.M)
        if not any(pat.search(src) for p, src in sources.items() if p != ctx.worktree / f):
            orphans.append(f)
    if orphans:
        return Verdict.deny("gate.structural.orphan", f"modules nothing imports: {orphans}")
    defs: dict[str, list[str]] = {}
    for p, src in sources.items():
        rel = p.relative_to(ctx.worktree).as_posix()
        if is_test_path(rel):
            continue
        for node in ast.parse(src).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                defs.setdefault(node.name, []).append(rel)
    dups = {k: v for k, v in defs.items() if len(v) > 1}
    if dups:
        return Verdict.deny("gate.structural.duplicate", f"defined in more than one module: {dups}")
    return Verdict.ok("gate.structural")


def final_verify(ctx: StopContext) -> Verdict:
    code, out = _run(ctx)
    if code == 5:
        return Verdict.deny("gate.final.has_tests", "no tests collected")
    if code != 0:
        return Verdict.deny("gate.final.green", f"full suite fails (exit {code}):\n{out[-800:]}")
    return Verdict.ok("gate.final")


STOP_GATES = {"red": red_verify, "scaffold": scaffold_verify, "green": green_verify,
              "structural_review": structural_verify, "final_verification": final_verify}
