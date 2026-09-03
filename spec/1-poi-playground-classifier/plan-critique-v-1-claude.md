# Implementation Plan Critique: 1-poi-playground-classifier (v1)

## Scope
Repository is completely empty (confirmed via direct inspection: no source files, no `CLAUDE.md`, no `spec/ARCHITECTURE.md`), so this critique verifies the plan against the spec text and against external API/library facts, not against existing code.

## FR/AR Coverage Audit [verified]
Mechanically cross-checked all 30 requirement IDs in spec.md (22 FRs + 8 ARs, confirmed by direct `grep`) against every task's `Covers:` line:

| ID | Covered by |
|---|---|
| FR-1.1, FR-1.2, FR-1.3 | Task 3 |
| AR-1.1 | Task 3 (consumer half) + Task 7 (ownership half) |
| FR-2.1, FR-2.3, AR-2.1 | Task 2 |
| FR-2.2 | Task 2 (produces the signal) + Task 6 (consumes it) |
| FR-3.1, FR-3.2, FR-3.3, AR-3.1, AR-3.2 | Task 4 |
| FR-4.1, FR-4.2, AR-4.1, AR-4.2 | Task 5 |
| FR-4.3, FR-4.4 | Task 6 |
| FR-4.5 | Task 7 |
| FR-5.1, FR-5.2 | Task 7 |
| FR-6.1, FR-6.2, FR-6.3 | Task 6 |
| FR-7.1, FR-7.2, AR-7.1, AR-7.2 | Task 7 |
| FR-8.1 | Task 8 |

All 30 IDs are covered by at least one task. No gaps.

## Parallelism Claim [verified]
Independently re-derived (and cross-checked against a second, isolated Codex pass given only the interface descriptions): Tasks 2–6 touch entirely disjoint files, and the one shared cross-task object (`get_context`) is created by neither Task 3 nor Task 4 — it's a parameter both accept, owned and constructed later in Task 7. This is exactly the right pattern to make the 5-way parallel claim true: no task needs to see another's code to be implemented or tested (Task 6 explicitly tests against fakes of the `PlaygroundClassifier` ABC and plain OSM booleans, never importing the concrete modules built in Tasks 2/4/5). The claim holds.

## External API/Library Accuracy [verified]
- Anthropic forced tool-choice syntax (`tool_choice={"type": "tool", "name": "..."}`) — matches current Anthropic Python SDK docs (re-verified via web search during plan-writing).
- Poetry `[tool.poetry.scripts]` entry-point format (`name = "package.module:function"`) — matches current Poetry docs (re-verified via web search during plan-writing).
- Overpass `out count;` response shape (`elements[0]["tags"]["total"]`, string-valued) — matches the live-tested result from the spec's own critique round (critique-v-2-codex.md actually executed this query against the production endpoint).
- Playwright sync API, `BrowserContext.set_default_timeout()`/`set_default_navigation_timeout()`, `APIRequestContext` redirect-following — all confirmed in the spec's own v1 critique round against current Playwright Python docs; the plan's Task 3/4 usage is consistent with those confirmed capabilities.

No hallucinated APIs found.

## Spec Deviations Claim [verified — accurate]
Re-checked the plan's "None identified" claim against its own task bodies. The candidate deviations I looked for and ruled out:
- src-layout vs. flat package: the spec doesn't prescribe a layout; this is a HOW-level choice.
- Exception-per-error-code hierarchy vs. sentinel returns: the spec's FRs say "return error: X" at the JSON-output level (FR-5.1), not at the level of internal function signatures — using exceptions internally and only converting to the JSON `error` field at the `cli.py` boundary (Task 7) is a faithful implementation of the same observable contract.
- `httpx`/Pillow as concrete choices: the spec's Constraints section itself suggests these exact libraries as examples ("e.g. `httpx`", "e.g. Pillow") — not a deviation, a direct following of the spec's own suggestion.
- Sync vs. async Playwright API: the spec never specifies this; sync is a reasonable, unremarkable default for a single-threaded CLI.

Agree with the plan: no deviation rows are missing.

## Verification Step Quality
Each task's `Verify:` line maps to concrete, executable checks and echoes the specific edge cases from the spec's own FR-level `Verify:` lines (e.g. Task 3 explicitly re-lists FR-1.2's `!3d`-vs-`@lat,lng` precedence case, the place-ID-triggers-browser case, and FR-1.3's three error cases including the new `TIMEOUT` one). One minor observation, non-blocking: Task 4's `Verify:` doesn't explicitly restate the `TIMEOUT` case from FR-3.3 (it lists "three tests, each mocking one failure condition" without naming all three) — trivial to confirm during implementation since FR-3.3 itself is unambiguous, not worth blocking on.

## Overall Assessment
No blocking items. This plan is ready to implement as written.
