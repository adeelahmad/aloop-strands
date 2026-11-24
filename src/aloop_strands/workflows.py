"""Pluggable workflows ("kernel modules"): workflows/<name>.yaml in the project's git declares
the steps, the role for each and the gate that must pass. Pinned like gates (pins.py)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .gates import stop_gates as sg
from .gates.base import Verdict
from .gates.tool_gates import ROLE_TOOLS


def tests_pass_verify(ctx: sg.StopContext) -> Verdict:
    if (v := sg._protected(sg.changed(ctx.worktree, ctx.base))):
        return v
    return sg.final_verify(ctx)


GATES = {"gate.red": sg.red_verify, "gate.scaffold": sg.scaffold_verify, "gate.green": sg.green_verify,
         "gate.structural": sg.structural_verify, "gate.final": sg.final_verify,
         "gate.tests_pass": tests_pass_verify}


@dataclass(frozen=True)
class Step:
    id: str
    role: str
    gate: str

    @property
    def sprint_level(self) -> bool:
        return self.role == "final_verification"


def load(root: Path | str, name: str) -> list[Step]:
    data = yaml.safe_load((Path(root) / "workflows" / f"{name}.yaml").read_text())
    steps = [Step(str(s["id"]), str(s["role"]), str(s["gate"])) for s in data["steps"]]
    for s in steps:
        if s.role not in ROLE_TOOLS or s.role == "supervisor":
            raise ValueError(f"workflow {name}: unknown role {s.role}")
        if s.gate not in GATES:
            raise ValueError(f"workflow {name}: unknown gate {s.gate}; every step needs a known gate")
    if not steps:
        raise ValueError(f"workflow {name} has no steps")
    return steps
