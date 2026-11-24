"""Role agents. Every agent, the supervisor included, is built here and only here, so the gate
intervention is attached to every worker (agents as tools / per-agent interventions:
https://strandsagents.com/docs/user-guide/sdk/multi-agent/agents-as-tools/ ,
https://strandsagents.com/docs/user-guide/sdk/agents/interventions/ )."""
from __future__ import annotations

from pathlib import Path

from strands import Agent

from .gates.intervention import GateIntervention
from .gates.tool_gates import ROLE_TOOLS
from .tools import make_tools

PROMPTS = {
    "supervisor": "You coordinate. You never write code. Gates decide acceptance.",
    "intake": "Turn the raw request into a five-part intent: actor, want, why, success, failure.",
    "standards": "Detect the stack and write the coding standard and gate matrix.",
    "planner": "Write the sprint plan: stories, tasks, tests, change targets.",
    "red": "Write failing tests for the task. Tests only; they must fail by assertion.",
    "scaffold": "Create each production symbol the tests reference once, as a stub.",
    "green": "Make the task's tests pass with the least code. Never change tests.",
    "structural_review": "Find orphan, duplicate or parallel code. Report only.",
    "final_verification": "Run the full test matrix and report.",
}


def build_agent(role: str, *, model, task: str, worktree: Path, project_root: Path, **tool_kw) -> Agent:
    if role not in ROLE_TOOLS:
        raise ValueError(f"unknown role {role!r}")
    tools = make_tools(ROLE_TOOLS[role], worktree, project_root, task, **tool_kw)
    return Agent(model=model, tools=tools, system_prompt=PROMPTS[role], callback_handler=None,
                 interventions=[GateIntervention(role, task, worktree, project_root)])
