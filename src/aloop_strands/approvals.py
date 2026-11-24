"""Human plan approval (aloop M0.9, reviewed mode).

The approval names one exact plan: its code is the first 8 hex of sha256 over the intent,
standards and sprint plan. Any change to those files voids it. Only host entry points (CLI,
dashboard) call `approve`; no agent has a tool that reaches it, and the dashboard binds to
loopback, which the Box policy does not let a worker reach.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .events import EventLog


class ApprovalRefused(PermissionError):
    pass


def plan_files(root: Path, sprint: int) -> list[Path]:
    d = Path(root) / "docs" / "agents"
    return [d / "intent.json", d / "standards.md", d / f"sprint{sprint}" / "plan.json"]


def plan_sha(root: Path, sprint: int) -> str:
    h = hashlib.sha256()
    for f in plan_files(root, sprint):
        h.update(f.name.encode() + b"\0")
        h.update(hashlib.sha256(f.read_bytes()).digest() if f.exists() else b"<missing>")
    return h.hexdigest()


def code_for(root: Path, sprint: int) -> str:
    return plan_sha(root, sprint)[:8]


def verified(root: Path, sprint: int) -> bool:
    """True only when every planning gate passed on exactly the current plan files (plan.ready)."""
    sha = plan_sha(root, sprint)
    ready = [e for e in EventLog(root).query(kind="plan.ready", sprint=sprint)]
    return bool(ready) and ready[-1].get("plan_sha") == sha


def approve(root: Path, sprint: int, typed: str, actor: str) -> dict:
    if not actor.startswith("human:"):
        raise ApprovalRefused("only a human entry point may approve")
    log = EventLog(root)
    sha = plan_sha(root, sprint)
    if not verified(root, sprint):
        log.append("approval.refused", rule="gate.approval", verdict="deny", sprint=sprint, actor=actor,
                   reason="no verified plan: planning gates have not passed on these exact files")
        raise ApprovalRefused(f"sprint {sprint} has no verified plan; run plan again")
    if typed.strip().lower() != sha[:8]:
        log.append("approval.refused", rule="gate.approval", verdict="deny", sprint=sprint, actor=actor,
                   reason="typed code does not match this plan")
        raise ApprovalRefused(f"code does not match the current plan of sprint {sprint}")
    return log.append("approval.granted", rule="gate.approval", verdict="allow", sprint=sprint,
                      plan_sha=sha, actor=actor, reason="human approved this exact plan")


def approved(root: Path, sprint: int) -> tuple[bool, str]:
    log = EventLog(root)
    ok, bad = log.verify()
    if not ok:
        return False, f"event log tampered at line {bad}"
    grants = list(log.query(kind="approval.granted", sprint=sprint))
    if not grants:
        return False, f"sprint {sprint} plan has no human approval"
    if grants[-1]["plan_sha"] != plan_sha(root, sprint):
        return False, f"sprint {sprint} plan changed since it was approved; approve it again"
    return True, "approved"


def planned_tasks(root: Path, sprint: int) -> set[str]:
    p = plan_files(root, sprint)[2]
    plan = json.loads(p.read_text())
    return {t["id"] for s in plan["stories"] for t in s["tasks"]}


def planned_task_map(root: Path, sprint: int) -> dict[str, dict]:
    plan = json.loads(plan_files(root, sprint)[2].read_text())
    return {t["id"]: t for s in plan["stories"] for t in s["tasks"]}
