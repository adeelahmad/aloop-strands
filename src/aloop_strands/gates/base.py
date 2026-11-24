"""Gate contract: a gate is a pure function GateContext -> Verdict.

`run_gate` makes every gate fail closed: an exception, or anything that is not a Verdict,
is a deny. `evaluate` runs gates in order, stops at the first deny, and logs every decision
(rule id, verdict, reason, role, task, tool, commit) to the hash-chained event log.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..events import ChainBroken, EventLog


@dataclass(frozen=True)
class Verdict:
    allow: bool
    rule: str
    reason: str = ""

    @classmethod
    def ok(cls, rule: str, reason: str = "") -> "Verdict":
        return cls(True, rule, reason)

    @classmethod
    def deny(cls, rule: str, reason: str) -> "Verdict":
        return cls(False, rule, reason)


@dataclass
class GateContext:
    role: str
    task: str
    project_root: Path
    worktree: Path
    tool: str
    input: dict[str, Any]
    log: EventLog
    now: float = field(default_factory=time.time)


Gate = Callable[[GateContext], Verdict]


def _name(gate: Gate) -> str:
    return getattr(gate, "rule", None) or f"gate.{getattr(gate, '__name__', 'anonymous')}"


def run_gate(gate: Gate, ctx: GateContext) -> Verdict:
    try:
        v = gate(ctx)
    except Exception as e:  # fail closed: a crashing gate blocks
        return Verdict.deny(f"{_name(gate)}.crash", f"gate raised {type(e).__name__}: {e}")
    if not isinstance(v, Verdict):
        return Verdict.deny(f"{_name(gate)}.crash", f"gate returned {type(v).__name__}, not a Verdict")
    return v


def head_commit(path: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "none"
    except Exception:
        return "none"


def evaluate(gates: list[Gate], ctx: GateContext) -> Verdict:
    passed: list[Verdict] = []
    verdict = None
    for g in gates:
        v = run_gate(g, ctx)
        if not v.allow:
            verdict = v
            break
        passed.append(v)
    if verdict is None:
        verdict = passed[0] if len(passed) == 1 else Verdict.ok(
            "+".join(v.rule for v in passed) or "gate.none", "every gate passed")
    rec = dict(rule=verdict.rule, verdict="allow" if verdict.allow else "deny", reason=verdict.reason,
               role=ctx.role, task=ctx.task, tool=ctx.tool, commit=head_commit(ctx.worktree))
    try:
        ctx.log.append("gate.decision", **rec)
    except ChainBroken as e:
        # A decision that cannot be recorded is not made: deny, and leave an unchained alarm.
        alarm = ctx.log.path.with_name("alarms.jsonl")
        with open(alarm, "a") as f:
            f.write(json.dumps({"ts": time.time(), "alarm": "log_integrity", "decision": rec}) + "\n")
        return Verdict.deny("gate.log_integrity", str(e))
    return verdict
