# Implementation Summary: 1-poi-playground-classifier

**Status:** Completed
**Date:** 2026-09-03
**Worktree:** `/Users/bastianellie/Documents/projects/python/playgrounds-on-the-route-worktrees/1-poi-playground-classifier` on branch `spec/1-poi-playground-classifier`

## Overview
Implemented the full CLI as specified: `input_resolver` classifies and resolves any of the four supported input forms to a `ResolvedPOI`; `osm_lookup` checks OpenStreetMap's Overpass API as a free, fast early-exit; on an inconclusive OSM result, `gmaps_scraper` retrieves photos via a shared Playwright browser context and `photo_classifier`'s `ClaudeVisionClassifier` classifies them one at a time via Anthropic's forced tool-choice; `decision_engine` applies the threshold/confidence-aggregation rule; `storage` persists evidence only for a confirmed positive; `cli.py` wires all of this into the `playground-check` entrypoint with the full FR-5/FR-7/AR-7 output and exit-code contract.

## Team Execution
No `TeamCreate`/`TaskCreate` tooling was available in this environment, so parallel work streams were run as direct background `Agent` tool calls, each with a strict file boundary, coordinated by me as the single point of integration.

| Stream | Files owned | Result |
|---|---|---|
| Task 1 (solo, by me) | `pyproject.toml`, `models.py`, `errors.py`, `conftest.py` | Scaffolding; verified `poetry run pytest` (0 tests) and imports |
| Task 2 (agent) | `osm_lookup.py` + test | 9 tests, no deviations |
| Task 3 (agent) | `input_resolver.py` + test | 15 tests, no deviations (used the exact signature I specified over `plan.md`'s stale one) |
| Task 4 (agent) | `gmaps_scraper.py` + test | 12 tests + 1 integration-marked, no deviations; `SELECTORS` are honestly documented as best-effort/unconfirmed (AR-3.1's accepted risk — this sandbox has no live browser access) |
| Task 5 (agent) | `photo_classifier.py` + test | 14 tests, no deviations |
| Task 6 (agent) | `decision_engine.py`, `storage.py` + tests | 11 tests, no deviations — and this agent caught a real coordination gap (see Deviations) |
| Task 7 (solo, by me) | `cli.py` + test | 11 tests; verified live against the real Overpass API |
| Task 8 (solo, by me) | `README.md`, spec adherence verification | Full suite green; 2 live smoke tests |

**Parallel phase:** Tasks 2–6 ran concurrently as five background agents immediately after Task 1 landed — confirmed zero file conflicts, exactly as the plan's critique predicted.
**Sequential phases:** Task 1 (scaffolding) before everything; Task 7 (integration) after all of 2–6 landed; Task 8 after Task 7.

## Files Created
- `pyproject.toml` — Poetry project config, dependencies, console script, pytest config
- `.gitignore`
- `README.md` — setup/usage/dev docs
- `src/playground_check/__init__.py`
- `src/playground_check/models.py` — `ResolvedPOI`, `Photo`, `ClassificationResult`, `ErrorCode`
- `src/playground_check/errors.py` — `PlaygroundCheckError` + 7 typed subclasses
- `src/playground_check/osm_lookup.py` — `check_nearby()`
- `src/playground_check/input_resolver.py` — `resolve()`
- `src/playground_check/gmaps_scraper.py` — `fetch_photos()`, `SELECTORS`
- `src/playground_check/photo_classifier.py` — `ClaudeVisionClassifier`
- `src/playground_check/decision_engine.py` — `PlaygroundClassifier` ABC, `Decision`, `decide_from_osm_hit()`, `decide_from_photos()`
- `src/playground_check/storage.py` — `save_evidence()`
- `src/playground_check/cli.py` — `main()`, `_build_parser()`, `_run()`, `_make_context_factory()`
- `tests/conftest.py`, `tests/test_*.py` (one per module, 8 files)

## Files Modified
- `src/playground_check/photo_classifier.py` — after initial landing, removed its independently-defined `PlaygroundClassifier` ABC and imported the one from `decision_engine.py` instead (see Deviations).

## Test Results
```
poetry run pytest -v
======================= 72 passed, 1 deselected in ~1s =======================
```
The 1 deselected test is `gmaps_scraper`'s `@pytest.mark.integration` live-Maps test, excluded by `pyproject.toml`'s default `-m "not integration"`. No test in the default run makes a real network/browser/API call.

**Live smoke tests** (exploratory, outside the automated suite, per the plan's Final Verification):
- `playground-check "40.7308,-73.9973" --vision-model claude-fake` → real Overpass API call → `{"label": "playground nearby", "method_used": "osm", "confidence": 1.0, ...}` — confirmed a genuine OSM-positive hit end to end.
- `playground-check "23.4162,25.6628" --vision-model claude-fake` (no `ANTHROPIC_API_KEY` set) → real Overpass API call (miss) → `{"error": {"code": "CONFIG_ERROR", ...}}`, exit code 0 — confirmed the fallback path and the credential gate both work, without needing a real Anthropic key or live browser access in this sandbox.
- `poetry run playground-check --help` → exit 0, all 9 flags listed, `--vision-model` marked required.

**Environment note:** `poetry install`'s own HTTP client hit an SSL cipher error reaching PyPI in this sandbox (unrelated to the project — plain `pip`/`curl` worked fine). Dependencies were installed via `pip` directly into the Poetry-managed virtualenv as a workaround; `pyproject.toml` itself is untouched and should install normally via `poetry install` on a machine without this sandbox-specific quirk.

## Spec Adherence

| Requirement | Status | Implementation | Test |
|---|---|---|---|
| FR-1.1 | Done | `input_resolver.resolve()` — classification regexes | `test_input_resolver.py` (4 classification-path tests) |
| FR-1.2 | Done | `input_resolver._resolve_coordinate/_resolve_full_url/_resolve_short_link/_resolve_via_browser/_extract_precise_coord` | `test_input_resolver.py` (`!3d/!4d` precedence, viewport fallback, short-link, free-text, browser-path tests) |
| FR-1.3 | Done | raises `InvalidInputError`/`PoiNotFoundError`/`OperationTimeoutError` | `test_input_resolver.py` (empty input, out-of-range coord, zero-result search incl. gibberish, hung navigation, hung HTTP fetch) |
| AR-1.1 | Done | `cli._make_context_factory()` (sole owner, lazy, memoized, `finally`-closed) + `input_resolver`/`gmaps_scraper` only ever call `get_context()` | `test_cli.py::test_context_factory_creates_exactly_one_context_and_closes_it` |
| FR-2.1 | Done | `osm_lookup.check_nearby()` — exact `nwr`/`out count` query | `test_osm_lookup.py` (query shape, `tags.total` parsing) |
| FR-2.2 | Done | `decision_engine.decide_from_osm_hit()` | `test_decision_engine.py` + `test_cli.py::test_osm_positive_end_to_end` (confirms `gmaps_scraper`/classifier never called) |
| FR-2.3 | Done | `osm_lookup.check_nearby()` swallows miss/timeout/error into `False` | `test_osm_lookup.py` (timeout/connection-error tests) |
| AR-2.1 | Done | `osm_lookup.py` imports only `httpx` | verified by inspection (module import list) |
| FR-3.1 | Done | `gmaps_scraper._load_poi_page()` | `test_gmaps_scraper.py` (navigation/timeout tests) |
| FR-3.2 | Done | `gmaps_scraper._collect_photos/_build_photo/_extract_photo_url/_normalize_photo_url` | `test_gmaps_scraper.py` (dedup/order/cap test) |
| FR-3.3 | Done | raises `NoPhotosAvailableError`/`ScrapeBlockedError`/`OperationTimeoutError` | `test_gmaps_scraper.py` (one test per condition) |
| AR-3.1 | Done | `gmaps_scraper.SELECTORS` (centralized; honestly flagged as best-effort/unconfirmed) | n/a — a documented risk, not a testable behavior |
| AR-3.2 | Done | `cli._make_context_factory()` sets `_USER_AGENT`/`_VIEWPORT`, non-persistent context | verified by inspection |
| FR-4.1 | Done | `decision_engine.PlaygroundClassifier` ABC, `photo_classifier.ClaudeVisionClassifier` | `test_photo_classifier.py::test_fake_classifier_subclass_drives_through_abc_interface` |
| FR-4.2 | Done | `photo_classifier.ClaudeVisionClassifier.classify()`, `_resize_if_needed()` | `test_photo_classifier.py` (positive/negative parse, resize-before-call) |
| FR-4.3 | Done | `decision_engine.decide_from_photos()` (threshold + min-confidence aggregation) | `test_decision_engine.py` (threshold=1, threshold=2/min, exhaustion→null) |
| FR-4.4 | Done | `decision_engine.decide_from_photos()` skip-and-continue / re-raise-if-all-fail | `test_decision_engine.py` + `test_photo_classifier.py` (per-call failure → `ClassifierError`) |
| FR-4.5 | Done | `cli._run()`'s `ANTHROPIC_API_KEY` check before `gmaps_scraper` | `test_cli.py::test_missing_api_key_returns_config_error_without_scraping` |
| AR-4.1 | Done | `ClaudeVisionClassifier.__init__` uses `anthropic.Anthropic()` zero-arg (SDK reads env var itself) | verified by inspection — no env var read/logged anywhere in the module |
| AR-4.2 | Done | `photo_classifier._TOOL_DEFINITION` (`strict: true`) + `tool_choice` | `test_photo_classifier.py::test_request_uses_forced_tool_choice_and_matching_tool_definition` |
| FR-5.1 | Done | `cli._run()` builds the envelope; `resolved` tracked via `poi is not None` | `test_cli.py` (success, `INVALID_INPUT`/`resolved=null`, `NO_PHOTOS_AVAILABLE`/`resolved` populated) |
| FR-5.2 | Done | `cli.main()`'s `--output-file` write | `test_cli.py::test_output_file_matches_stdout` |
| FR-6.1 | Done | `storage.save_evidence()` (per-run dir, slug, sidecars) | `test_storage.py::test_gmaps_positive_decision_writes_photos_and_sidecars` |
| FR-6.2 | Done | `storage.save_evidence()`'s early-return check | `test_storage.py` (OSM and GMaps-negative cases) |
| FR-6.3 | Done | `storage.save_evidence()`'s per-item `try/except OSError` | `test_storage.py::test_one_write_failure_is_skipped_and_does_not_raise` |
| FR-7.1 | Done | `cli._build_parser()` | `test_cli.py::test_help_exits_0_and_lists_every_flag`, `test_omitting_vision_model_exits_2_...` |
| FR-7.2 | Done | `cli._run()`'s pipeline wiring | `test_cli.py` (OSM-positive and GMaps-fallback end-to-end tests) |
| AR-7.1 | Done | `cli._validate_args()` | `test_cli.py::test_config_invariants_reject_bad_threshold` |
| AR-7.2 | Done | `cli.main()`'s exit-code logic + `_run()`'s `INTERNAL_ERROR` envelope | `test_cli.py::test_unhandled_exception_becomes_internal_error_and_exit_1` + manual verification of exit 0 for `CONFIG_ERROR` |
| FR-8.1 | Done | `pyproject.toml`'s `pytest` config (`-m "not integration"` default) | `poetry run pytest` — 72 passed, 1 deselected, offline |

All 30 requirements implemented and tested. No skipped or partially-implemented requirements.

## Deviations from Spec
None from the spec itself. One **coordination gap in my own execution**, caught by the Task 6 agent and fixed before integration:

- **`PlaygroundClassifier` ABC duplication.** I told the Task 6 (`decision_engine`) agent to define the shared `PlaygroundClassifier` ABC in `decision_engine.py`, but didn't tell the Task 5 (`photo_classifier`) agent to import it from there — so it independently defined its own, identically-shaped ABC locally. Both agents did exactly what they were individually told; the gap was mine as coordinator. Fixed post-hoc: `photo_classifier.py` now imports `PlaygroundClassifier` from `decision_engine` instead of redefining it (one shared identity, verified via `P1 is P2` and the full suite staying green at 72 passed).

No `ADR.md` was needed — this was an execution-coordination fix, not an architectural deviation from the spec.
