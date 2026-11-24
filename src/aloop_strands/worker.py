"""Worker entry point that runs INSIDE a Strands Box: `python -m aloop_strands.worker '<json spec>'`.

The box mounts only the task worktree (read-write, gate dirs denied) and an outbox for this worker's
own gate log; its only network route is the model endpoint. The same gate intervention runs here as
layer 1; the box policy is layer 2; the host's stop gates, run after this process exits, decide.
Commands go through the box's shell (`sh`), so the Dogwood policy checks each one.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

from . import roles


def shell_executor(cmd, cwd, timeout):
    return subprocess.run(["sh", "-c", shlex.join(cmd)], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def main(argv: list[str] | None = None) -> int:
    spec = json.loads((argv or sys.argv[1:])[0])
    if spec.get("script") is not None:
        from .scripted import ScriptedModel
        model = ScriptedModel(spec["script"])
    else:
        from .models import make_model
        model = make_model(spec["role"])
    from .budget import BudgetIntervention, Budgets
    from .events import EventLog
    b = spec.get("budgets") or {}
    bi = BudgetIntervention(Budgets(**b) if b else Budgets(), EventLog(Path(spec["outbox"])), spec["role"],
                            spec["task"], Path(spec["worktree"]))
    bi.run_before = 0  # the host passes the remaining run budget
    agent = roles.build_agent(spec["role"], model=model, task=spec["task"], worktree=Path(spec["worktree"]),
                              project_root=Path(spec["outbox"]), check_pins=False,
                              test_cmd=spec["test_cmd"], executor=shell_executor, extra_interventions=[bi])
    result = agent(spec["prompt"])
    u = agent.event_loop_metrics.accumulated_usage
    print(json.dumps({"stop_reason": str(result.stop_reason), "role": spec["role"], "task": spec["task"],
                      "total_tokens": int(u.get("totalTokens", 0)),
                      "breach": {"rule": bi.breached.rule, "reason": bi.breached.reason} if bi.breached else None}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
