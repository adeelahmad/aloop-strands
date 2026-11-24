"""The door rule (aloop M9.30): a one-way door (irreversible: deploy, release, push to the default
branch, data deletion, secrets, CI config) is crossed only under the human's recorded authorization;
a wall (weakening the gates themselves) never; a two-way door is decided, not asked."""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from . import approvals
from .events import EventLog
from .gates.tool_gates import PROTECTED

CATALOGUE = [  # (door, pattern on the task's words); first match wins
    ("push_default", re.compile(r"\bpush\b.*\b(main|master|default branch)\b", re.I)),
    ("release", re.compile(r"\b(release|publish|pypi|npm publish)\b", re.I)),
    ("deploy", re.compile(r"\b(deploy|production|prod)\b", re.I)),
    ("data_delete", re.compile(r"\b(drop|truncate|delete|wipe)\b.*\b(table|database|data|bucket|records?)\b", re.I)),
    ("secret", re.compile(r"\b(secret|credential|api key|password|token)s?\b", re.I)),
    ("spend", re.compile(r"\b(purchase|buy|pay|billing|spend)\b", re.I)),
]
WALLS = {"gate_change"}


class DoorRefused(PermissionError):
    pass


def classify(task: dict) -> str | None:
    targets = [str(t) for t in task.get("targets", []) or []] + [str(t) for t in task.get("tests", []) or []]
    for t in targets:
        parts = PurePosixPath(t).parts
        if parts and parts[0] in PROTECTED:
            return "gate_change"
        if t.startswith(".github/workflows"):
            return "ci_config"
    words = " ".join([str(task.get("title", "")), str(task.get("intent", "")), *map(str, task.get("actions", []) or [])])
    for door, pat in CATALOGUE:
        if pat.search(words):
            return door
    return None


def is_wall(door: str | None) -> bool:
    return door in WALLS


def code_for(root, task_id: str, door: str) -> str:
    sprint = int(re.match(r"^S(\d+)-", task_id).group(1))
    return hashlib.sha256(f"{approvals.plan_sha(root, sprint)}|{task_id}|{door}".encode()).hexdigest()[:8]


def authorize(root, task_id: str, door: str, typed: str, actor: str) -> dict:
    log = EventLog(root)
    if not actor.startswith("human:"):
        raise DoorRefused("only a human may authorize a one-way door")
    if is_wall(door):
        log.append("door.refused", rule="gate.door", verdict="deny", task=task_id, door=door, reason="a wall is never crossed")
        raise DoorRefused(f"{door} is a wall")
    if typed.strip().lower() != code_for(root, task_id, door):
        log.append("door.refused", rule="gate.door", verdict="deny", task=task_id, door=door, actor=actor,
                   reason="typed code does not match")
        raise DoorRefused("code does not match this task and plan")
    return log.append("door.authorized", rule="gate.door", verdict="allow", task=task_id, door=door, actor=actor,
                      code=typed.strip().lower(), reason="human authorized this one-way door")


def authorized(root, task_id: str, door: str) -> bool:
    code = code_for(root, task_id, door)
    return any(e.get("code") == code for e in EventLog(root).query(kind="door.authorized", task=task_id, door=door))
