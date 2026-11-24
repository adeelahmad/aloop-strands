# aloop-strands: standing goal

**What it is.** aloop (Aladdin's Loop) reimplemented as a standalone, deployable agent app on the
Strands Harness SDK. Reference implementation: github.com/adeelahmad/aloop. *Models explore. Gates decide.*

**Rules.**
- Read the current Strands docs before using an API; cite the doc page in a comment where you use it.
- A model saying "done" is never a result. Only deterministic gates decide.
- TDD on this repo: failing test first, then code.
- Every gate fails closed (a crashing gate blocks). Never weaken a gate, an invariant or a test to make progress.
- Workers never read or modify their own gate config; gates/workflows/box files are hash-pinned.

**Flywheel.** Orient (docs/ROADMAP.md, open PRs) → pick the highest-value unblocked item (red CI first) →
branch, tests first, draft PR, drive green → put evidence in the PR → update ROADMAP status → repeat.
Keep user-facing replies to 1–2 words unless more is needed.
