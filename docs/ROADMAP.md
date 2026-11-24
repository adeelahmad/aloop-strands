# Roadmap

Design (approved 2026-11-24): https://claude.ai/artifact/9DSDszMsA1WZGfWXLHqo6g

Stack: Python, `strands-agents` 1.66.0, `strands-harness` 0.5.0. Dogwood runs at the Strands Box
layer (`box/policy.dw`); the Python SDK has no Dogwood intervention, so in-process temporal rules
are our own deterministic gates.

| Milestone | Exit criterion | Status |
| --- | --- | --- |
| M0 Skeleton | package, CLI, hash-chained event log, pins; tamper tests | ✅ |
| M1 Gates | every gate: pass, block, crash-blocks, tamper tests; no handler fails open; Box spike | ✅ gates (Box spike in M3) |
| M2 Plan | no execution role before approval of the current plan sha; changed plan voids it | ✅ |
| M3 Execute | e2e "a semver parser" → merged code on main inside Box; negative e2e (gate edit, out-of-worktree write, commit without tests) blocked and logged | ✅ scripted model, in Box locally; live model run blocked (no key) |
| M4 Mission, budgets | one-way door waits for a human token; hard budget breach stops and discards | ✅ |
| M5 Ship | Dockerfile, compose, DEPLOY.md (EKS, AgentCore), conformance report vs aloop ROADMAP §6 | ⬜ |

## Known gaps (from the design)
- In-SDK Dogwood is TypeScript-only (experimental): temporal rules are a Python gate + Box policy.
- Box file rules cover only the box shell's commands; the profile filesystem list is the real fence.
- Commit-after-tests as a Box temporal rule is unproven (M1 spike); fallback: host commits.
- No USD cost cap in Strands; budget gate on AfterModelCallEvent, overshoot ≤ 1 model call.
