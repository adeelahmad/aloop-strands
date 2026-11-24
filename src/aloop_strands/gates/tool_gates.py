"""Tool-call gates, run before every worker tool call (see intervention.py)."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from .base import GateContext, Verdict

# Which tools each role may call at all. The supervisor coordinates and can never write.
ROLE_TOOLS: dict[str, frozenset[str]] = {
    "supervisor": frozenset({"read_file", "list_files"}),
    "intake": frozenset({"read_file", "list_files", "write_artifact"}),
    "standards": frozenset({"read_file", "list_files", "write_artifact"}),
    "planner": frozenset({"read_file", "list_files", "write_artifact"}),
    "mission_planner": frozenset({"read_file", "list_files", "write_artifact"}),
    "red": frozenset({"read_file", "list_files", "write_file", "edit_file", "run_tests"}),
    "scaffold": frozenset({"read_file", "list_files", "write_file", "edit_file", "run_tests"}),
    "green": frozenset({"read_file", "list_files", "write_file", "edit_file", "run_tests"}),
    "developer": frozenset({"read_file", "list_files", "write_file", "edit_file", "run_tests"}),
    "structural_review": frozenset({"read_file", "list_files", "run_tests", "write_report"}),
    "final_verification": frozenset({"read_file", "list_files", "run_tests", "write_report"}),
}
WRITE_TOOLS = frozenset({"write_file", "edit_file"})
# Never visible to a worker, read or write: gate config, workflows, box policy, run state, git internals.
PROTECTED = ("gates", "workflows", "box", ".aladdin", ".agentic", ".git")


def role_gate(ctx: GateContext) -> Verdict:
    allowed = ROLE_TOOLS.get(ctx.role)
    if allowed is None:
        return Verdict.deny("gate.role", f"unknown role {ctx.role!r}")
    if ctx.tool not in allowed:
        return Verdict.deny("gate.role", f"role {ctx.role} may not call {ctx.tool}")
    return Verdict.ok("gate.role")


def _paths(ctx: GateContext) -> list:
    out = []
    if "path" in ctx.input:
        out.append(ctx.input["path"])
    if "paths" in ctx.input:
        p = ctx.input["paths"]
        out.extend(p if isinstance(p, list) else [p])
    return out


def _resolve(ctx: GateContext, raw) -> tuple[Path | None, str]:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None, f"path must be a non-empty string, got {type(raw).__name__}"
    wt = Path(os.path.realpath(ctx.worktree))
    p = Path(raw)
    full = Path(os.path.realpath(p if p.is_absolute() else wt / p))  # follows symlinks
    try:
        full.relative_to(wt)
    except ValueError:
        return None, f"{raw} resolves outside the task worktree"
    return full, ""


def scope_gate(ctx: GateContext) -> Verdict:
    wt = Path(os.path.realpath(ctx.worktree))
    for raw in _paths(ctx):
        full, why = _resolve(ctx, raw)
        if full is None:
            return Verdict.deny("gate.scope", why)
        rel = full.relative_to(wt).parts
        if rel and rel[0] in PROTECTED:
            return Verdict.deny("gate.integrity", f"{raw} is gate/run state; workers cannot read or change it")
    return Verdict.ok("gate.scope")


def is_test_path(rel: str) -> bool:
    p = PurePosixPath(rel)
    return (p.parts[:1] == ("tests",) or p.name.startswith("test_") or p.stem.endswith("_test")
            or p.name == "conftest.py" or ".test." in p.name or ".spec." in p.name)


ARTIFACT_DIRS = {"write_artifact": "docs/agents/", "write_report": "docs/agents/reports/"}


def role_files_gate(ctx: GateContext) -> Verdict:
    if ctx.tool in ARTIFACT_DIRS:
        wt = Path(os.path.realpath(ctx.worktree))
        for raw in _paths(ctx):
            full, why = _resolve(ctx, raw)
            if full is None:
                return Verdict.deny("gate.scope", why)
            if not (full.relative_to(wt).as_posix() + "/").startswith(ARTIFACT_DIRS[ctx.tool]):
                return Verdict.deny("gate.artifact_scope", f"{ctx.tool} writes only under {ARTIFACT_DIRS[ctx.tool]}")
        return Verdict.ok("gate.role_files")
    if ctx.tool not in WRITE_TOOLS:
        return Verdict.ok("gate.role_files")
    wt = Path(os.path.realpath(ctx.worktree))
    for raw in _paths(ctx):
        full, why = _resolve(ctx, raw)
        if full is None:
            return Verdict.deny("gate.scope", why)
        rel = full.relative_to(wt).as_posix()
        if ctx.role == "red" and not is_test_path(rel):
            return Verdict.deny("gate.red.tests_only", f"RED writes tests only, not {rel}")
        if ctx.role in ("green", "scaffold") and is_test_path(rel):
            return Verdict.deny(f"gate.{ctx.role}.no_tests", f"{ctx.role.upper()} may not change test file {rel}")
    return Verdict.ok("gate.role_files")


COMMIT_WINDOW_S = 3600


def commit_gate(ctx: GateContext) -> Verdict:
    """Temporal rule: commit only if this exact tree passed its tests within the last hour,
    with no failing run after it. Mirrors the Box policy.dw rule at the host layer.
    RED and SCAFFOLD commits record a red state: they need `expect="fail"`, a failing run of
    this exact tree recorded by the host within the hour."""
    if ctx.tool != "commit":
        return Verdict.ok("gate.commit_after_tests")
    ok, bad = ctx.log.verify()
    if not ok:
        return Verdict.deny("gate.commit_after_tests", f"event log tampered at line {bad}")
    tree = ctx.input.get("tree")
    last = None
    for e in ctx.log.query(task=ctx.task):
        if e["kind"] in ("tests.passed", "tests.failed") and e.get("tree") == tree:
            last = e
    want = "tests.failed" if ctx.input.get("expect") == "fail" else "tests.passed"
    if last is None or last["kind"] != want:
        return Verdict.deny("gate.commit_after_tests",
                            f"no {'failing' if want == 'tests.failed' else 'passing'} test run for this exact tree")
    if ctx.now - last["ts"] > COMMIT_WINDOW_S:
        return Verdict.deny("gate.commit_after_tests", "last passing test run is older than one hour")
    return Verdict.ok("gate.commit_after_tests")


TOOL_GATES = [role_gate, scope_gate, role_files_gate, commit_gate]
