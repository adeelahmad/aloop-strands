# Conformance report: aloop host conformance suite

Measured against aloop `docs/ROADMAP.md` §6 (20 items). Status values follow aloop §8:
`IMPLEMENTED_TESTED` (evidence test named), `IMPLEMENTED_UNTESTED`, `BLOCKED_HOST`, `NOT_IMPLEMENTED`.
Run all evidence: `ALOOP_BOX_BIN=<box> pytest -rs` (CI runs it on every push, Box job included).

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 1 | Intent admitted and normalized | IMPLEMENTED_TESTED | `test_plan_approval.py::test_intake_gate_requires_five_parts` |
| 2 | Planner artifacts schema-valid | IMPLEMENTED_TESTED | `test_plan_approval.py::test_plan_gate_rejects_bad_plans`, `test_standards_gate_requires_test_command`, `test_failed_planning_gate_stops_before_approval` |
| 3 | RED rejected if tests pass first run | IMPLEMENTED_TESTED | `test_stop_gates.py::test_red_blocks_when_tests_pass`, `test_red_blocks_no_new_tests` |
| 4 | RED cannot write production code | IMPLEMENTED_TESTED | `test_stop_gates.py::test_red_blocks_production_code`, `test_gates_unit.py::test_red_may_write_tests_only` |
| 5 | GREEN cannot suppress tests | IMPLEMENTED_TESTED | `test_stop_gates.py::test_green_blocks_suppressions`, `test_green_blocks_weakened_test`, `test_green_blocks_new_conftest_monkeypatch`, `test_e2e.py::test_negative_worker_weakens_test` |
| 6 | A failed gate blocks acceptance | IMPLEMENTED_TESTED | `test_gates_unit.py::test_crashing_gate_denies`, `test_intervention.py::test_crashing_gate_blocks_tool`, `test_stop_gates.py::test_runner_crash_blocks` |
| 7 | Worktrees stay isolated | IMPLEMENTED_TESTED | `test_gates_unit.py::test_scope_denies_escape`, `test_scope_denies_symlink_escape`; in Box: `test_box.py::test_box_blocks_write_outside_worktree` |
| 8 | Accepted GREEN is mergeable | IMPLEMENTED_TESTED | `test_e2e.py::test_semver_intent_reaches_main`, `test_semver_in_box_reaches_main` |
| 9 | Hard-breached work discarded | IMPLEMENTED_TESTED | `test_m4.py::test_hard_token_budget_stops_worker_and_discards_work` |
| 10 | Transcript and event refs exist | IMPLEMENTED_TESTED (events) | Hash-chained `.aladdin/logs/events.jsonl`: `test_events.py` (7 tests); every gate decision: `test_gates_unit.py::test_evaluate_logs_every_decision`. Full per-attempt transcripts are not stored yet (gap R1). |
| 11 | Lineage complete | IMPLEMENTED_TESTED | intent → plan sha → approval code → task → gate decisions with commit → `merge` event: `test_plan_approval.py::test_plan_change_after_approval_voids_it`, `test_e2e.py::test_semver_intent_reaches_main` |
| 12 | Usage exact or marked estimated | IMPLEMENTED_TESTED | `usage` events carry `total_tokens`, `estimated: false`, `model`: `test_m4.py::test_usage_is_logged_per_attempt` |
| 13 | Hard time and token limits stop the worker | PARTIAL: tokens IMPLEMENTED_TESTED, time NOT_IMPLEMENTED | `test_m4.py::test_hard_token_budget_stops_worker_and_discards_work`; wall-clock limit is gap R2 (Box process timeout is the planned backend) |
| 14 | Admission closes deterministically | IMPLEMENTED_TESTED | `test_m4.py::test_run_budget_closes_admission` |
| 15 | Breached task re-decomposed | NOT_IMPLEMENTED | Breach blocks and discards; re-planning a breached task is gap R3 (planner behaviour in aloop too) |
| 16 | Handoff produced, context rotated | NOT_IMPLEMENTED | Each attempt is a fresh agent (no carried context), but no handoff file is written: gap R4 |
| 17 | Compaction only when advertised | IMPLEMENTED_UNTESTED | Workers use Strands' default conversation manager and no summarizing compaction is configured; a test asserting this is gap R5 |
| 18 | Model route observable | IMPLEMENTED_TESTED | `test_models_cli.py::test_route_by_role`, `test_default_route`; `usage` event records `model` |
| 19 | Memory injection equivalent across hosts | N/A | Single host (Strands); no memory injection layer yet |
| 20 | Semantic capability gaps explicit | IMPLEMENTED_TESTED | Missing/old Box is a `BoxError`, never a silent pass: `test_box.py::test_box_config_error_raises_not_a_result`, `test_models_cli.py::test_cli_run_refuses_without_box`; this table lists every gap |

**Score:** 15 tested, 1 partial, 1 untested, 2 not implemented, 1 N/A.

Feature acceptance beyond §6 (from the build brief):

| Acceptance | Status | Evidence |
|---|---|---|
| Unit tests for every gate incl. fail-closed and tamper | ✅ | `test_gates_unit.py`, `test_intervention.py`, `test_pins.py`, `test_events.py` |
| e2e semver: approval → RED → GREEN → review → main, in Box, no human after approval | ✅ scripted model | `test_e2e.py::test_semver_in_box_reaches_main`, `test_no_human_input_after_approval` |
| Same e2e with a live model | BLOCKED | No model credential in the build environment (see ROADMAP) |
| Negative e2e: gate edit, escape, commit without tests blocked and logged | ✅ | `test_e2e.py::test_negative_*`, `test_box.py::test_box_blocks_*`, `test_box_commit_needs_passing_tests_first` |
