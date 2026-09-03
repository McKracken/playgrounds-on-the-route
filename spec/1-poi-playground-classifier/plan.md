# Implementation Plan: 1-poi-playground-classifier

**Status:** Ready
**Date:** 2026-09-03
**Spec:** spec/1-poi-playground-classifier/spec.md
**Plan Critique:** spec/1-poi-playground-classifier/plan-critique-consolidated-v-1.md — no blocking items; one non-blocking wording fix applied (Task 4's Verify line).

## Overview
Build the CLI from scratch as a Poetry-packaged Python 3.12 project (`playground_check`). One foundational task establishes the package layout, shared data types, and the exception-based error-propagation pattern every other module uses. Five independent modules (`osm_lookup`, `input_resolver`, `gmaps_scraper`, `photo_classifier`, `decision_engine`+`storage`) are then built in parallel against those shared interfaces, each with its own test file. A final `cli.py` wires them together, and a closing task verifies the full suite runs offline and writes minimal setup docs.

## Readiness
- Checklist: all items `[x]`/`[N/A]` in spec.md — no open gaps blocking planning.
- Open questions: none outstanding after 4 critique rounds (see spec's Change Log).
- External API/library verification:
  - Overpass QL `nwr`/`out count` syntax — live-tested against the production endpoint during spec critique (see critique-v-2-codex.md).
  - Anthropic vision limits (10MB base64, 8000×8000px) and forced tool-choice syntax (`tool_choice={"type": "tool", "name": "..."}`) — verified directly against current Claude Platform docs.
  - Playwright Python `BrowserContext.set_default_timeout()`/`set_default_navigation_timeout()`, locator screenshots, `APIRequestContext` redirect-following — verified against current Playwright Python docs.
  - Poetry `[tool.poetry.scripts]` console-script entry-point syntax — verified against current Poetry docs.
- Repository state: clean, git-tracked, no commits yet (per earlier `git status`); no source code, `CLAUDE.md`, or `spec/ARCHITECTURE.md` exist. **Architecture context is missing** — recommend running `/spec-init` after this feature lands to backfill `spec/ARCHITECTURE.md` and `CLAUDE.md` from the resulting code, since this is the first spec in the repo.

## Code Impact
- **New package:** `src/playground_check/` (src-layout), with `models.py`, `errors.py`, `osm_lookup.py`, `input_resolver.py`, `gmaps_scraper.py`, `photo_classifier.py`, `decision_engine.py`, `storage.py`, `cli.py`.
- **New tests:** `tests/` mirroring the module list, plus `tests/conftest.py` for shared fixtures.
- **New config:** `pyproject.toml` (Poetry, `[tool.poetry.scripts]`, `pytest` config with `-m "not integration"` default), `README.md` (setup: `poetry install`, `playwright install chromium`, `ANTHROPIC_API_KEY`).
- **No database, no existing API/UI to modify** — greenfield CLI.

### Shared interfaces (fixed here so parallel tasks don't need to coordinate)
- **Error propagation:** one exception hierarchy in `errors.py` — `PlaygroundCheckError(Exception)` with `.code: str` and `.message: str` attributes, and one subclass per error code in Data Requirements' enum (`InvalidInputError`, `PoiNotFoundError`, `NoPhotosAvailableError`, `ScrapeBlockedError`, `OperationTimeoutError`, `ClassifierError`, `ConfigError`). `INTERNAL_ERROR` is not a named subclass — it's `cli.py`'s catch-all for any *other* unhandled exception (AR-7.2). Every module raises the typed exception instead of returning a sentinel; `cli.py` (Task 7) is the only place that catches them and builds the FR-5.1 JSON envelope.
- **Browser context ownership (AR-1.1):** `cli.py` owns a single memoizing factory, e.g. `get_context: Callable[[], BrowserContext]`, created once and passed into both `input_resolver.resolve()` and `gmaps_scraper.fetch_photos()`. Neither module calls `playwright.sync_api.sync_playwright()` or `Browser.new_context()` itself — they only ever call `get_context()`, which returns the same instance on every call within one CLI invocation. This lets Tasks 3 and 4 be built independently against a shared, fixed signature.
- **Playwright API style:** use the **sync API** (`playwright.sync_api`) throughout — this is a single-threaded CLI with no concurrent page operations, so sync is simpler and is Playwright's own recommendation for this case.
- **Data types (Task 1, used everywhere):** `ResolvedPOI`, `Photo`, `ClassificationResult` as frozen `dataclasses`; error codes as a `str, Enum` (`ErrorCode`) whose members serialize directly to the exact strings in Data Requirements' enum.

## Project Constraints
- Python 3.12 minimum; Poetry for packaging; `argparse` (stdlib) for the CLI — no Click/Typer (Constraints).
- No project-specific conventions exist yet (no `CLAUDE.md`/`spec/ARCHITECTURE.md`) — this plan establishes the baseline patterns (src-layout, dataclasses for value types, exception-per-error-code) for future specs to follow.
- The six module boundaries in the spec's Constraints section (`input_resolver`, `osm_lookup`, `gmaps_scraper`, `photo_classifier`, `decision_engine`, `storage`) must stay independently importable — each gets its own file with no circular imports; only `cli.py` imports all of them.
- `ANTHROPIC_API_KEY` is read via the Anthropic SDK's own environment-variable default — never threaded through as a function argument beyond what the SDK client needs (AR-4.1).

## Implementation Strategy
- Size: **Large** (8 tasks across 4 domains: scaffolding, resolution/geodata, scraping/classification, orchestration).
- Execution mode: 1 solo task → **5 parallel streams** → 1 solo integration task → 1 solo verification task.
- Parallelizable work: Tasks 2, 3, 4, 5, 6 touch entirely disjoint files and depend only on Task 1's shared types/interfaces — zero file overlap, safe to run concurrently.
- Sequential blockers: Task 1 must finish before anything else (defines the shared types/exceptions every module imports). Task 7 must wait for all of Tasks 2–6 (it imports and wires all of them). Task 8 must wait for Task 7 (runs the full assembled suite).

## Implementation Tasks

### Task 1: Project scaffolding and shared types
**Goal:** A `poetry install`-able package with the shared data model and error hierarchy every other task builds against.
**Files:** `pyproject.toml`, `src/playground_check/__init__.py`, `src/playground_check/models.py`, `src/playground_check/errors.py`, `tests/__init__.py`, `tests/conftest.py`
**Dependencies:** None
**Do:**
- `poetry init`-equivalent `pyproject.toml`: Python `^3.12`, dependencies `httpx`, `playwright`, `anthropic`, `pillow`; dev-dependency `pytest`; `[tool.poetry.scripts] playground-check = "playground_check.cli:main"`; `[tool.pytest.ini_options]` with `markers = ["integration: ..."]` and `addopts = "-m 'not integration'"`.
- `models.py`: frozen dataclasses `ResolvedPOI(lat: float, lng: float, name: str | None, maps_url: str)`, `Photo(bytes: bytes, mime_type: str, source_url: str | None)`, `ClassificationResult(is_playground: bool, confidence: float)`; `ErrorCode(str, Enum)` with the 8 members from Data Requirements.
- `errors.py`: `PlaygroundCheckError` base (`code: ErrorCode`, `message: str`) and one subclass per non-`INTERNAL_ERROR` code, per the Shared Interfaces section above.
- `tests/conftest.py`: a `fake_context_factory` fixture (returns a no-op stand-in satisfying the `get_context` signature) for reuse by Tasks 3/4/7's tests.
**Verify:** `poetry install` succeeds; `poetry run python -c "import playground_check"` succeeds; `poetry run pytest` runs (0 tests, exit 0).
**Covers:** Constraints (Python/Poetry/argparse), Data Requirements (`ResolvedPOI`, `Photo`, `ClassificationResult`, error codes).
**Notes:** Get the exception hierarchy right here — every parallel task in Tasks 2–6 depends on it.

### Task 2: `osm_lookup` module
**Goal:** Free, fast Overpass-based playground detection.
**Files:** `src/playground_check/osm_lookup.py`, `tests/test_osm_lookup.py`
**Dependencies:** Task 1
**Do:**
- `check_nearby(lat: float, lng: float, radius: float, timeout: float, endpoint: str) -> bool` (the `hit` result) — builds the `nwr(around:...)["leisure"="playground"];out count;` query from spec's FR-2.1, sends it via `httpx` with a descriptive `User-Agent`, client timeout = `timeout + 5`.
- Parse `hit` from `elements[0]["tags"]["total"]` (string) per FR-2.1's documented response shape — not `len(elements)`.
- On any `httpx` timeout/connection/HTTP error, or a parse failure, treat as inconclusive (return `False`/a sentinel `osm_lookup` distinguishes from a real negative) rather than raising — per FR-2.3, this must fall through to scraping, not error out.
**Verify:** `pytest tests/test_osm_lookup.py` — mocked nonzero-count response → `True`; mocked zero-count → `False`; mocked timeout/connection error → falls through (no exception propagates); a `node`/`way` vs. simulated `relation`-inclusive count are treated identically.
**Covers:** FR-2.1, FR-2.2 (the `hit` signal it produces), FR-2.3, AR-2.1.
**Notes:** No Playwright, no Anthropic import — keep this module's dependency footprint to `httpx` only (AR-2.1).

### Task 3: `input_resolver` module
**Goal:** Turn any of the four supported input forms into a `ResolvedPOI`, using the browser only when genuinely necessary.
**Files:** `src/playground_check/input_resolver.py`, `tests/test_input_resolver.py`
**Dependencies:** Task 1
**Do:**
- `resolve(raw_input: str, get_context: Callable[[], BrowserContext], *, timeout: float) -> ResolvedPOI` — classify via the FR-1.1 patterns (URL / short-link / coordinate regex / free-text catch-all).
- Coordinate path: validate range/finiteness (FR-1.2); raise `InvalidInputError` on failure.
- URL/short-link path: extract `!3d<lat>!4d<lng>` first, then `@lat,lng`, per FR-1.2's precedence; follow short-link redirects via `httpx` with a 5-second timeout (raise `OperationTimeoutError` on timeout).
- Anything left without an extractable coordinate (place-ID URL, query-string URL, free text): call `get_context()`, navigate/search via the sync Playwright API, read the resolved page's own URL/state back for `lat`/`lng`/`name`; raise `PoiNotFoundError` on no result, `OperationTimeoutError` on a Playwright timeout.
- Bare coordinates: synthesize `maps_url = f"https://www.google.com/maps?q={lat},{lng}"`.
**Verify:** `pytest tests/test_input_resolver.py` — one test per FR-1.2's Verify line (both-`!3d`-and-`@`-present prefers `!3d`; viewport-only URL used directly; place-ID-only URL triggers the browser path via a mocked Playwright page; out-of-range coordinate → `InvalidInputError`; bare coordinate → synthesized non-empty `maps_url`); FR-1.3's three error cases (empty input, zero-result search, mocked hung navigation/HTTP call).
**Covers:** FR-1.1, FR-1.2, FR-1.3, AR-1.1 (the `input_resolver`-side half of the shared-context contract).

### Task 4: `gmaps_scraper` module
**Goal:** Retrieve up to N deduplicated, full-resolution playground-candidate photos from a POI's Maps page.
**Files:** `src/playground_check/gmaps_scraper.py`, `tests/test_gmaps_scraper.py`
**Dependencies:** Task 1
**Do:**
- `fetch_photos(poi: ResolvedPOI, get_context: Callable[[], BrowserContext], *, max_photos: int) -> list[Photo]` — receives the context via `get_context()` (never launches its own, per AR-1.1/FR-3.1), navigates to `poi.maps_url`.
- A single `SELECTORS` dict (AR-3.1) for the photo-gallery entry point, image elements, and consent/CAPTCHA detection — placeholder selectors to be confirmed against the live site during this task (documented as a discovery step, since Google's DOM can't be pinned in the spec).
- Per-item extraction: prefer the underlying image URL from the DOM; fall back to an element screenshot only when no URL is extractable. Dedup by normalized URL/content hash. Scroll until `max_photos` reached or two consecutive scrolls add nothing new.
- Raise `NoPhotosAvailableError` (zero photos found), `ScrapeBlockedError` (CAPTCHA/consent interstitial detected), or `OperationTimeoutError` (any step past the context's configured timeout) per FR-3.3.
**Verify:** `pytest tests/test_gmaps_scraper.py` — mocked Playwright page with a known photo count (incl. duplicates) returns `min(unique, max_photos)` in retrieval order; three tests, one per FR-3.3 error condition (`NO_PHOTOS_AVAILABLE`, `SCRAPE_BLOCKED`, `TIMEOUT`); one `@pytest.mark.integration` test against a real public POI URL (skipped by default).
**Covers:** FR-3.1, FR-3.2, FR-3.3, AR-3.1, AR-3.2.

### Task 5: `photo_classifier` module
**Goal:** A pluggable playground-detection interface with a Claude-vision implementation.
**Files:** `src/playground_check/photo_classifier.py`, `tests/test_photo_classifier.py`
**Dependencies:** Task 1
**Do:**
- `PlaygroundClassifier` ABC: `classify(self, photo: Photo) -> ClassificationResult`.
- `ClaudeVisionClassifier(model: str)`: resize `photo.bytes` (Pillow) if needed to stay under 10MB base64/8000×8000px; call `anthropic.Anthropic().messages.create(model=..., tools=[<is_playground/confidence schema>], tool_choice={"type": "tool", "name": "classify_playground"}, ...)`; parse the forced `tool_use` block's input into a `ClassificationResult`.
- On any API error or an unparseable/missing tool-use response, raise `ClassifierError` for that single call (caller — Task 6 — decides skip-vs-abort per FR-4.4).
**Verify:** `pytest tests/test_photo_classifier.py` — mocked `tool_use` response (positive and negative) parses into `ClassificationResult`; oversized fake image is resized before the mocked API call is inspected; a fake classifier implementing the ABC is driven correctly with no `isinstance` check anywhere in this module.
**Covers:** FR-4.1, FR-4.2, AR-4.1, AR-4.2.

### Task 6: `decision_engine` + `storage` modules
**Goal:** Combine the OSM signal and photo classifications into the final label/confidence, and persist evidence only for a confirmed positive.
**Files:** `src/playground_check/decision_engine.py`, `src/playground_check/storage.py`, `tests/test_decision_engine.py`, `tests/test_storage.py`
**Dependencies:** Task 1 (types/interfaces only — does not import `osm_lookup`, `gmaps_scraper`, or the concrete `ClaudeVisionClassifier`; tested against fakes/mocks, wired to the real modules in Task 7)
**Do:**
- `decision_engine.py`: given an OSM `hit=True` → immediate `("playground nearby", "osm", 1.0, [])` (FR-2.2). Otherwise, given a `PlaygroundClassifier` and a photo iterator, classify one at a time (skipping `ClassifierError`s, raising `ClassifierError` only if every photo fails per FR-4.4), stop at `threshold` positives, aggregate `confidence` as `min()` of qualifying results or `None` if threshold never met (FR-4.3).
- `storage.py`: given the final label and the in-memory qualifying `Photo`+`ClassificationResult` pairs, write image files + JSON sidecars to `<output-dir>/<slug>-<timestamp>/` only when the label is positive via the GMaps path (FR-6.1); no-op for OSM-only/negative (FR-6.2); catch and log (stderr) any individual write failure without raising, omitting that item from the returned evidence-path list (FR-6.3).
**Verify:** `pytest tests/test_decision_engine.py` — threshold=1 stops after first positive; threshold=2 with confidences `[0.9, 0.6]` → `0.6`; exhausted photos → `confidence=None`; one failing classification among three doesn't abort; all failing → `ClassifierError`. `pytest tests/test_storage.py` — threshold=2/1-of-3-positive (final negative) writes zero files; a met-threshold case writes photos+sidecars with all required fields; a mocked write failure for one of two photos still returns the other in the evidence list.
**Covers:** FR-2.2 (consumption side), FR-4.3, FR-4.4 (aggregation/skip logic), FR-6.1, FR-6.2, FR-6.3.

### Task 7: `cli.py` orchestration
**Goal:** Wire every module into the single `playground-check` entrypoint with the full FR-7/AR-7 contract.
**Files:** `src/playground_check/cli.py`, `tests/test_cli.py`
**Dependencies:** Tasks 2, 3, 4, 5, 6
**Do:**
- `argparse` parser with all flags from FR-7.1 (`--vision-model` required, no default; `--osm-endpoint` default per FR-2.1; the rest per their stated defaults); validate AR-7.1's numeric invariants immediately after parsing (argparse error + exit 2 on violation).
- `main()`: create the `get_context` memoizing factory (not yet called); call `input_resolver.resolve(...)`; call `osm_lookup.check_nearby(...)`; on inconclusive, check `ANTHROPIC_API_KEY` presence (raise `ConfigError` per FR-4.5 before touching `gmaps_scraper`); call `gmaps_scraper.fetch_photos(...)` then `decision_engine`; call `storage` for evidence; always close the browser context (if created) in a `finally`.
- Catch every `PlaygroundCheckError` subclass and build the FR-5.1 JSON envelope (`resolved=null` iff resolution itself didn't complete, per FR-5.1's exact rule); catch any other exception as `INTERNAL_ERROR` with the full FR-5.1 envelope shape (not a shortcut), per AR-7.2.
- Print exactly one JSON document to stdout (`json.dumps`, pretty-printed if `--output-file` also given); route all logging to stderr; exit 0 for any well-formed result (success or defined error), 1 only for the `INTERNAL_ERROR` path, 2 for pre-pipeline argparse/AR-7.1 failures (already argparse's default).
**Verify:** `pytest tests/test_cli.py` — `--help` exits 0 and lists every flag; omitting `--vision-model` exits 2 before any pipeline stage runs (mock-assert no module called); one fully-mocked OSM-positive end-to-end run and one fully-mocked GMaps-fallback end-to-end run each produce the correct JSON envelope and exit code.
**Covers:** FR-4.5, FR-5.1, FR-5.2, FR-7.1, FR-7.2, AR-7.1, AR-7.2, AR-1.1 (the `cli.py`-ownership half: lazy creation, single context per invocation, `finally`-block close).

### Task 8: Full-suite verification and setup docs
**Goal:** Confirm the assembled tool actually satisfies FR-8.1 and is installable/runnable by someone new to the repo.
**Files:** `README.md`; no new source files
**Dependencies:** Task 7
**Do:**
- Run the complete `pytest` suite offline (network disabled) and confirm a clean pass with `-m "not integration"` as the default selection.
- Spot-check `poetry run playground-check --help` and one fully-mocked-free smoke path if feasible without live cost (e.g. an OSM-positive case against a real, known playground-adjacent coordinate — no Anthropic/Playwright cost).
- Write a short `README.md`: `poetry install`, `playwright install chromium`, `export ANTHROPIC_API_KEY=...`, example invocation, and a one-line note on the accepted Google ToS risk (mirroring the spec's Constraints).
**Verify:** `poetry run pytest` passes with zero failures/errors and zero real network calls in the default run; `poetry run playground-check --help` exits 0.
**Covers:** FR-8.1 (final confirmation across all modules), Constraints (dependency/setup documentation).

## Final Verification
- `poetry install && poetry run pytest` — full suite green, offline.
- `poetry run playground-check --help` — exits 0, lists all flags.
- Manual smoke test (outside the automated suite, real cost): one OSM-positive coordinate (e.g. a known urban park) and one GMaps-fallback case, run with a real `--vision-model` and `ANTHROPIC_API_KEY`, to confirm the two paths work end-to-end against live services — this is exploratory, not part of the shipped test suite (Playwright/Anthropic/Overpass are all mocked in `pytest`).

## Documentation
- Living docs: none exist yet (`spec/docs/` not present). After this lands, running `/spec-init` is worth doing to backfill `CLAUDE.md` and `spec/ARCHITECTURE.md` from the new code, since every future spec in this repo will benefit from having those conventions captured.

## Spec Deviations
None identified — every implementation choice in this plan (src-layout, dataclasses, an exception-per-error-code hierarchy, a memoizing context-factory function, sync Playwright API, `httpx`/Pillow as the spec's own suggested example libraries) is a HOW-level detail consistent with the spec's WHAT, not a change to observable behavior or acceptance criteria.

## Risks
- **Google Maps DOM is unknown until implementation time** (AR-3.1) — Task 4's `SELECTORS` are placeholders confirmed by live inspection during that task, not before. If Google's anti-automation defenses block the dev environment entirely, Task 4 may need more iteration than estimated; the spec already scopes this as an accepted, ongoing risk (Assumptions & Risks).
- **Live-service costs during manual verification** — the Task 8 smoke test and any ad hoc debugging of Task 4/5 against real Google Maps/Anthropic incur real latency and (for Anthropic) real API cost; keep manual runs to a handful of POIs, and prefer a cheap vision model for exploratory testing.
- **No existing `CLAUDE.md`/`spec/ARCHITECTURE.md`** means this plan is also implicitly setting the repo's first conventions (src-layout, exception-based errors, dataclass value types) — reasonable defaults, but worth confirming with `/spec-init` once the code exists, in case a different convention is preferred going forward.
- **Rollback:** greenfield project, no production data or users — if an approach in Tasks 2–6 proves unworkable, the affected module can be reworked in isolation without touching the others, since each is independently testable per the spec's own module-boundary requirement.
