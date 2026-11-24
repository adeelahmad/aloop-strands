"""Planning stop gates: intake, standards, planner artifacts must be well formed before the
plan can be shown for approval."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from .base import Verdict
from .tool_gates import PROTECTED

INTENT_FIELDS = ("actor", "want", "why", "success", "failure")
TASK_ID = re.compile(r"^S(\d+)-\d{2}-T\d+$")
WORKFLOWS = {"tdd", "standard"}


def _docs(root) -> Path:
    return Path(root) / "docs" / "agents"


def intake_verify(root) -> Verdict:
    f = _docs(root) / "intent.json"
    try:
        intent = json.loads(f.read_text())
    except (OSError, ValueError) as e:
        return Verdict.deny("gate.intake", f"intent.json unreadable: {e}")
    missing = [k for k in INTENT_FIELDS if not intent.get(k)]
    if missing:
        return Verdict.deny("gate.intake", f"intent lacks: {', '.join(missing)}")
    return Verdict.ok("gate.intake")


def standards_verify(root) -> Verdict:
    f = _docs(root) / "standards.md"
    if not f.exists():
        return Verdict.deny("gate.standards", "standards.md missing")
    if not re.search(r"^test_command:\s*\S+", f.read_text(), re.M):
        return Verdict.deny("gate.standards", "standards.md declares no test_command")
    return Verdict.ok("gate.standards")


def _safe_rel(p) -> bool:
    if not isinstance(p, str) or not p or p.startswith("/"):
        return False
    parts = PurePosixPath(p).parts
    return ".." not in parts and parts[0] not in PROTECTED


def plan_verify(root, sprint: int) -> Verdict:
    f = _docs(root) / f"sprint{sprint}" / "plan.json"
    try:
        plan = json.loads(f.read_text())
        if plan.get("workflow") not in WORKFLOWS:
            return Verdict.deny("gate.plan", f"unknown workflow {plan.get('workflow')!r}")
        stories = plan["stories"]
        if not stories:
            return Verdict.deny("gate.plan", "plan has no stories")
        seen = set()
        for s in stories:
            if not s.get("tasks"):
                return Verdict.deny("gate.plan", f"story {s.get('id')} has no tasks")
            for t in s["tasks"]:
                m = TASK_ID.match(str(t.get("id", "")))
                if not m or int(m.group(1)) != sprint or t["id"] in seen:
                    return Verdict.deny("gate.plan", f"bad or duplicate task id {t.get('id')!r}")
                seen.add(t["id"])
                for key in ("tests", "targets"):
                    vals = t.get(key)
                    if not vals or not all(_safe_rel(v) for v in vals):
                        return Verdict.deny("gate.plan", f"task {t['id']}: {key} missing or outside the project")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        return Verdict.deny("gate.plan", f"plan.json invalid: {type(e).__name__}: {e}")
    return Verdict.ok("gate.plan")
