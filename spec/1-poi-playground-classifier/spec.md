# Spec 1: POI Playground Classifier (CLI)

## Overview
A Python CLI that takes a single Google Maps Point of Interest (POI) — given as a Maps URL, short link, coordinates, or free-text name — and classifies it as `"playground nearby"` or `"no playground nearby"`. It first checks OpenStreetMap for a tagged playground within a short radius as a free, fast early-exit; if that's inconclusive, it falls back to opening the POI in Google Maps via a headless browser, pulling photos, and classifying them with Claude vision until enough positive photos are found or photos run out.

## Goals
- Classify a single POI as having a nearby kid's playground or not, from a CLI invocation.
- Minimize cost/latency by using a free geospatial signal (OSM) before falling back to browser scraping + paid vision calls.
- Keep every module (input resolution, OSM lookup, Maps scraping, photo classification, decision logic, storage) independently testable and swappable, since the photo classifier is expected to be replaced later and the whole tool may become a backend service.

---

## Feature 1: POI Input Resolution

**Who & why:** The user has a POI in whatever form Google Maps happened to hand them — a shared full link, a `maps.app.goo.gl` short link, raw coordinates copied from a GPS app, or just a place name they remember — and needs one command that accepts any of these without manual conversion first.

### Functional Requirements

#### FR-1.1: Accept and classify input format
The CLI accepts a single positional input string. `input_resolver` first strips surrounding whitespace, then classifies the remaining string into exactly one of: full Google Maps URL, Google Maps short link (`maps.app.goo.gl/...` or `goo.gl/maps/...`), a syntactic coordinate pair (matches `^-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?$`), or free-text place name/address (the catch-all for any non-empty string matching none of the other three patterns). An empty/whitespace-only string is not classified further — it goes directly to FR-1.3's `INVALID_INPUT` path.
**Verify:** Unit tests feed each of the four input forms plus an empty string and assert the resolver dispatches to the matching branch (or directly to `INVALID_INPUT` for the empty case).

#### FR-1.2: Resolve to a canonical POI
Whatever the input form, `input_resolver` produces a canonical `ResolvedPOI` value with `lat`, `lng`, `name` (best-effort; may be `None` for bare coordinates), and `maps_url`. Resolution uses the cheapest mechanism that works, escalating only when needed:
- **Coordinate pairs** are validated before use (latitude finite and within `[-90, 90]`, longitude finite and within `[-180, 180]`) and used directly — no browser needed. A syntactic match that fails validation is `INVALID_INPUT` (FR-1.3).
- **A full Maps URL with an extractable precise coordinate** uses that coordinate directly — no browser needed. Precision source, in order: (1) the `data=` parameter's `!3d<lat>!4d<lng>` pair, if present — this is the POI's actual location; (2) the `@lat,lng` viewport segment, used only when no `!3d/!4d` pair is present — this is just the map's last pan/zoom center and can drift from the true POI, so it's a fallback, not a preferred source.
- **A short link** has its HTTP redirect followed (no browser, no JS execution) to obtain the resolved long URL; the same precise-coordinate extraction above is then applied to it.
- **Everything else that still needs resolving** — a full Maps URL or resolved short-link URL with no extractable precise coordinate (whether it carries a place ID, an embedded search query, or both), and free text — is resolved by navigating it (or, for free text, submitting the URL-encoded text as a Maps search) in the shared Playwright browser context (AR-1.1), then reading `lat`/`lng`/`name` back from the resulting page's own canonical state. This is the one resolution path that needs a browser, and it's the same mechanism regardless of whether the input was a URL, a place ID, or free text.
- **Bare coordinate pairs** get a synthesized `maps_url` (`https://www.google.com/maps?q=<lat>,<lng>`), since none was given in the input — `maps_url` is never absent on a successfully resolved `ResolvedPOI`.
**Verify:** A unit test with a URL containing both a `!3d/!4d` pair and a different `@lat,lng` viewport asserts the `!3d/!4d` coordinates are used, not the viewport; a unit test with a mocked short-link redirect that resolves to a URL with only a `@lat,lng` viewport (no `!3d/!4d`) asserts those coordinates are used with no browser call; a unit test with a mocked short-link redirect that resolves to a place-ID-only URL (no coordinates at all) asserts the browser-navigation path is used instead; given free text `"Eiffel Tower"`, an integration test (network-gated, skipped by default) asserts the resolved coordinates are within ~0.01° of its known location; a unit test with an out-of-range coordinate pair (e.g. `"200,9"`) asserts `INVALID_INPUT` with no browser call; a unit test with a bare valid coordinate pair asserts `maps_url` is synthesized and non-empty.

#### FR-1.3: Handle unresolvable input
- **`INVALID_INPUT`**: empty/whitespace-only input, or a coordinate-pattern match with an out-of-range or non-finite value. Both are detectable without ever attempting resolution.
- **`POI_NOT_FOUND`**: any URL, short link, or free-text input that, after the resolution steps in FR-1.2 are attempted, yields no valid Maps result (a browser navigation/search that finds nothing).
- **`TIMEOUT`**: the shared browser context's navigation/action timeout, or the short-link HTTP client's 5-second timeout (both defined in AR-1.1), is exceeded while resolution is in progress.
**Verify:** A unit test with empty/whitespace-only input returns `INVALID_INPUT` with no resolution attempted; a unit test with an out-of-range coordinate pair returns `INVALID_INPUT`, not `POI_NOT_FOUND`; a unit test with a mocked zero-result free-text search — including plausible gibberish free text — returns `POI_NOT_FOUND`, not `INVALID_INPUT`; a unit test with a mocked hung browser navigation (or a mocked hung short-link redirect fetch) returns `error: TIMEOUT` with `resolved=null`.

### Architectural Requirements

#### AR-1.1: Browser ownership and shared timeout
`input_resolver` depends on an HTTP client (for short-link redirect resolution, bounded by its own fixed 5-second timeout — it's a single lightweight HTTP call, not a full page load) and, for any input that isn't a bare valid coordinate pair or a URL with an extractable precise coordinate (per FR-1.2's `!3d/!4d`-then-`@lat,lng` precedence), the shared Playwright browser context described here. `cli.py` (FR-7.2) is the **sole owner** of that context: it creates it lazily on first need — whichever of `input_resolver`'s browser-based resolution path or `gmaps_scraper` (Feature 3) runs first for a given invocation — and passes that same context to whichever module needs it. No other module ever calls Playwright's launch or close APIs. The context's default navigation/action timeout is set to `--page-timeout` at creation time, so resolution-stage and scraping-stage browser operations share the same bound. Exactly one context is created per CLI invocation; it is closed in a `finally` block at the end of the run regardless of which stages executed. Inputs resolved without a browser (bare coordinates, or a URL/short-link with explicit destination coordinates) combined with an OSM hit (Feature 2) never trigger browser creation at all.

---

## Feature 2: OSM Fast-Path Playground Detection

**Who & why:** OpenStreetMap already has explicit `leisure=playground` tags with coordinates for a large share of mapped playgrounds. Checking that first is free, fast, and needs no browser automation — letting most clear-positive cases skip scraping and paid vision calls entirely.

### Functional Requirements

#### FR-2.1: Query Overpass for tagged playgrounds
Given the resolved POI's `lat`/`lng`, `osm_lookup` issues an Overpass QL query against a configurable endpoint (`--osm-endpoint`, default `https://overpass-api.de/api/interpreter`), requesting `node`, `way`, **and `relation`** elements tagged `leisure=playground` within a configurable radius (`--osm-radius`, default **150** meters), using the combined `nwr` selector so relation-tagged playgrounds (e.g. multipolygons) aren't missed, and `out count;` since only existence — not geometry — is needed:
```
[out:json][timeout:{server_timeout}];
nwr(around:{radius},{lat},{lng})["leisure"="playground"];
out count;
```
The response places the count in a single `elements[0]` entry of `"type": "count"`, with the tallies as string values under `tags` (e.g. `tags.total`) — `hit` is computed from that field (`int(elements[0]["tags"]["total"]) > 0`), not from `len(elements)` (always 1 for a count query, regardless of the actual tally).
**Verify:** A unit test with a mocked Overpass response reporting a nonzero count returns `hit=True`; a response reporting zero returns `hit=False`; a unit test asserts a `relation`-only match (simulated via the mocked count) is treated identically to a `node`/`way` match.

#### FR-2.2: Early-exit on positive hit
If `hit=True`, `decision_engine` immediately finalizes the label as `"playground nearby"` with `method_used="osm"`, `confidence=1.0`, empty `evidence`, and skips Google Maps scraping and photo classification entirely.
**Verify:** An integration test with a mocked positive Overpass response asserts `gmaps_scraper` and `photo_classifier` are never called.

#### FR-2.3: Fall through on no-hit or Overpass failure
If Overpass returns `hit=False`, times out, or errors, `osm_lookup` returns an **inconclusive** result (never a final negative), and the pipeline proceeds to the Google Maps photo path (Feature 3). `--osm-timeout` (default **10 seconds**) sets the Overpass query's own `[timeout:{server_timeout}]` value; the HTTP client's request timeout is set to `--osm-timeout + 5` seconds, so the server has a chance to return its own timeout error before the client aborts the connection.
**Verify:** A unit test simulating an Overpass timeout/connection error asserts the pipeline proceeds to call `gmaps_scraper` rather than returning a negative label directly.

### Architectural Requirements

#### AR-2.1: Module isolation
`osm_lookup` depends only on an HTTP client — no Playwright, no Anthropic dependency — so it is unit-testable and reusable in isolation.

---

## Feature 3: Google Maps Photo Retrieval

**Who & why:** When OSM data is missing or inconclusive (common outside dense urban areas), the only signal available without a paid Maps API is the POI's own photo gallery — reached the way a person would, through a real (headless) browser.

### Functional Requirements

#### FR-3.1: Open the POI page in Google Maps
`gmaps_scraper` receives the shared Playwright browser context — created and owned exclusively by `cli.py`, per AR-1.1, with its default navigation/action timeout already set to `--page-timeout` (default **20** seconds) at creation time — and navigates to `ResolvedPOI.maps_url` (per FR-1.2, always populated — either the input's own URL or a synthesized coordinate query). `gmaps_scraper` itself never launches or closes a browser/context. The scraper waits for the main POI panel to render before proceeding.
**Verify:** A network-gated integration test (skipped by default) navigates to a known public POI URL and asserts the POI panel is detected within the timeout.

#### FR-3.2: Extract up to N photos
From the loaded POI page, `gmaps_scraper` opens the photo gallery (if present) and, for each gallery item, attempts to extract the underlying full-resolution photo URL from the DOM; an element screenshot is used only as a fallback when no direct image URL can be extracted for a given item (thumbnail screenshots are lower quality and hurt classification accuracy, so direct URL extraction is preferred whenever available). Items are deduplicated by normalized photo URL (or a content hash, for items with no stable URL) before counting toward `--max-photos` (default **20**). The gallery is scrolled to load additional items until either `--max-photos` unique photos have been collected, or two consecutive scroll actions yield no new unique photos (exhaustion) — whichever comes first. Photos are passed to classification in the order they were retrieved from the gallery.
**Verify:** A unit test against a mocked Playwright page (fake DOM with a known photo count, including duplicates) asserts exactly `min(unique_available_photos, max_photos)` deduplicated images are returned, in retrieval order.

#### FR-3.3: Handle zero photos, blocked automation, and timeouts
If the POI page loads but exposes zero photos, return `error: NO_PHOTOS_AVAILABLE`. If Google Maps presents a CAPTCHA or consent-blocking interstitial that prevents reaching the POI panel, return `error: SCRAPE_BLOCKED`. If any step exceeds the timeout configured in FR-3.1, return `error: TIMEOUT`.
**Verify:** Three unit tests, each mocking one failure condition, assert the matching error code is returned and no photos reach the classifier.

### Architectural Requirements

#### AR-3.1: Centralized, isolated selectors
All Google Maps DOM selectors (photo gallery entry point, image elements, consent/CAPTCHA detection) live in one place within `gmaps_scraper` (e.g. a single `SELECTORS` constant), not scattered through the module. Google Maps' DOM is unofficial, undocumented, and can change without notice — this spec cannot pin exact selector strings; the implementer must inspect the live page at implementation time and keep selectors isolated for easy updates when Google changes markup.

#### AR-3.2: Stateless, low-footprint browser use
Playwright runs headless with a realistic desktop user-agent and viewport, using the single non-persistent browser context created per invocation (AR-1.1) — no stored cookies/login state — consistent with the "no API account, low call volume, general precaution" constraint from the requirements.

---

## Feature 4: Playground Photo Classification

**Who & why:** The user wants an MVP that needs no training corpus, but also wants the door open to swap in a cheaper/specialized model later without rewriting the pipeline around it.

### Functional Requirements

#### FR-4.1: Pluggable classifier interface
`photo_classifier` exposes an abstract interface — `PlaygroundClassifier.classify(photo: Photo) -> ClassificationResult`, where `ClassificationResult` has `is_playground: bool` and a **required** `confidence: float` in `[0, 1]` — with exactly one v1 implementation (`ClaudeVisionClassifier`). A classification that fails outright (FR-4.4) does not produce a `ClassificationResult` at all; failure is represented by the photo being skipped, never by a null or missing confidence on a result object. `decision_engine` and `cli` must interact with classifiers only through this interface (no concrete-class checks), so a future local/specialized model can be substituted without touching either.
**Verify:** A unit test provides a fake classifier implementing the interface and confirms `decision_engine` drives it correctly with no `isinstance` checks against `ClaudeVisionClassifier`.

#### FR-4.2: Classify photos via Claude vision
`ClaudeVisionClassifier` sends each photo as a base64-encoded image content block (JPEG, PNG, GIF, or WebP) in a Messages API call to the model given by the required `--vision-model` flag (see FR-7.1 — this flag has no default; the CLI fails argument parsing if it's omitted). Before encoding, a photo is resized if needed so its base64-encoded payload stays under **10MB** and its pixel dimensions stay within **8000×8000** — the current direct-API limits — rather than the previously-assumed 20MB. The classifier obtains a structured response via Anthropic's forced tool-choice (tool use) mechanism (see AR-4.2), asking whether the image shows kid-playground equipment (slides, swings, jungle gyms, climbing structures, etc.).
**Verify:** A unit test with a mocked Anthropic tool-use response asserts both a positive and a negative structured response parse correctly into `ClassificationResult`; a unit test with an oversized fake image asserts it is resized before encoding.

#### FR-4.3: Threshold-based early stop and confidence aggregation
`decision_engine` classifies fetched photos one at a time, in retrieval order, and stops as soon as the count of `is_playground=True` results reaches `--threshold` (default **1**) — it does not classify remaining photos once the threshold is met. When the threshold is met, the final `confidence` is the **minimum** confidence value among the qualifying (`is_playground=True`) photos (the weakest link among the positive evidence used). If the photo cap is exhausted without meeting the threshold, the label is `"no playground nearby"` and `confidence` is `null`.
**Verify:** A unit test with a fake classifier and `threshold=1` asserts classification stops immediately after the first positive result; a unit test with `threshold=2` and confidences `[0.9, 0.6]` for the two qualifying photos asserts final `confidence=0.6`; a test that exhausts all photos without reaching threshold asserts `confidence=null`.

#### FR-4.4: Handle classifier failures
If a classification call fails (network error, rate limit, malformed response) for a given photo, that photo is skipped (not counted as positive or negative) and classification continues with the next photo. If every fetched photo fails to classify, return `error: CLASSIFIER_ERROR`.
**Verify:** A unit test mocks one failing call among three photos and asserts classification continues past it; a test where all calls fail asserts `CLASSIFIER_ERROR` is returned.

#### FR-4.5: Fail fast on missing credentials
Immediately after `osm_lookup` returns an inconclusive result — before invoking `gmaps_scraper` (Feature 3) — `cli.py` verifies that `ANTHROPIC_API_KEY` is set. If it is absent, return `error: CONFIG_ERROR` without invoking `gmaps_scraper` or performing any further Playwright work. (Resolution, per FR-1.2, may already have used the shared browser context before this check runs — this check only guarantees no *additional* Playwright work happens for scraping.)
**Verify:** A unit test with the environment variable unset asserts `gmaps_scraper` is never invoked and `CONFIG_ERROR` is returned.

### Architectural Requirements

#### AR-4.1: Credential handling
The Anthropic API key is read from the `ANTHROPIC_API_KEY` environment variable (standard SDK behavior) and is never accepted as a CLI argument, logged, or included in any output, sidecar, or debug log — only its presence/absence (FR-4.5) is ever observable.

#### AR-4.2: Structured output via tool use
`ClaudeVisionClassifier` uses Anthropic's forced tool-choice (tool use) mechanism with an explicit JSON schema (`is_playground: boolean`, `confidence: number` in `[0, 1]`, both required) to obtain the classification result, rather than parsing free-text prose — making the response deterministically parseable and testable regardless of prompt wording changes. Where supported, the tool definition sets `strict: true` for guaranteed schema conformance; FR-4.4's malformed-response handling remains as defense in depth for responses that still don't conform.

---

## Feature 5: Classification Decision & Output Contract

**Who & why:** Whichever path produces the answer, the user (or a future caller — script, backend, app) needs one predictable, parseable result shape, including on failure.

### Functional Requirements

#### FR-5.1: Unified JSON output
The CLI prints one JSON object to stdout with: `input` (raw, always present), `resolved` (`{lat, lng, name, maps_url}` or `null`), `label` (`"playground nearby"` | `"no playground nearby"` | `null`), `method_used` (`"osm"` | `"gmaps_photos"` | `null`), `confidence` (float or `null`), `evidence` (list of saved evidence file paths, `[]` if none), and `error` (`null` on success, else `{code, message}` where `code` is one of the error codes enumerated in Data Requirements).
`resolved` is `null` if and only if input resolution (Feature 1) itself failed to produce a `ResolvedPOI` — this covers `INVALID_INPUT`, `POI_NOT_FOUND`, and a `TIMEOUT` that occurs during resolution itself (e.g. a hung free-text search). For every other error (`NO_PHOTOS_AVAILABLE`, `SCRAPE_BLOCKED`, a `TIMEOUT` during scraping, `CLASSIFIER_ERROR`, `CONFIG_ERROR`), resolution already succeeded, so `resolved` is populated and `label`/`method_used`/`confidence` are `null`.
**Verify:** Unit tests validate the output JSON's keys/types for: a success case, an `INVALID_INPUT` case (`resolved=null`), and a `NO_PHOTOS_AVAILABLE` case (`resolved` populated, `label=null`).

#### FR-5.2: Optional output file
An optional `--output-file <path>` flag additionally writes the same JSON object to disk, pretty-printed; when omitted, output goes to stdout only.
**Verify:** A unit test invoking the CLI with `--output-file` asserts the written file's content matches stdout's JSON.

---

## Feature 6: Evidence Persistence

**Who & why:** The user wants to visually spot-check positive classifications, and wants the option — not the obligation — to later reuse saved photos as training data for a specialized classifier, which means tagging them with structured metadata now rather than just dumping image files.

### Functional Requirements

#### FR-6.1: Save qualifying photos with metadata only after the final decision
Photos classified `is_playground=True` are held in memory (not written to disk) as classification proceeds. Only once `decision_engine` finalizes the label as `"playground nearby"` via the GMaps-photos path does `storage` write those in-memory positive photos to a per-run output directory, plus a JSON metadata sidecar per photo containing: POI id/coordinates, photo source URL, timestamp, classifier name/model used, and confidence. The output directory defaults to `./output/<slug>-<run-timestamp>/` (overridable via `--output-dir`), where `<slug>` is `ResolvedPOI.name` if present (else its coordinates), lowercased, with any character outside `[a-z0-9-]` replaced by `-`.
**Verify:** A unit test running the pipeline with `threshold=2` where only 1 of 3 mocked photos is positive (final label negative) asserts zero files are written, even though one photo classified positive mid-run; a test where the threshold is met asserts the qualifying photo(s) and matching sidecars exist with all required fields populated.

#### FR-6.2: No files written for OSM-only or negative results
When `method_used="osm"`, or the final label is `"no playground nearby"`, no evidence files are written — only the Feature 5 JSON output exists.
**Verify:** A unit test for an OSM-positive case and a GMaps-negative case both assert the output directory is never created/populated.

#### FR-6.3: Evidence write failures don't change the classification result
If writing an evidence photo or its metadata sidecar fails (e.g. disk full, permission denied), the failure is logged to stderr and that photo is simply omitted from the `evidence` list in the output — it does not alter the already-determined `label`/`confidence`, and does not raise a separate error code.
**Verify:** A unit test mocks a filesystem write failure for one of two qualifying photos and asserts the CLI still exits successfully with `label="playground nearby"` and `evidence` containing only the one photo that wrote successfully.

---

## Feature 7: CLI & Configuration

**Who & why:** A single entrypoint that wires every module together, with every tunable parameter from the requirements exposed and overridable rather than hardcoded.

### Functional Requirements

#### FR-7.1: Command-line interface
A single console-script entrypoint (e.g. `playground-check`), built with `argparse`, accepts a positional `input` argument and flags: `--osm-radius` (default 150), `--osm-timeout` (default 10), `--osm-endpoint` (default `https://overpass-api.de/api/interpreter`), `--max-photos` (default 20), `--threshold` (default 1), `--vision-model` (**required, no default**), `--page-timeout` (default 20), `--output-dir`, `--output-file`. `--help` lists all flags, showing defaults for every flag that has one and marking `--vision-model` as required.
**Verify:** A subprocess-based test runs `playground-check --help`, asserts exit code 0, and asserts every documented flag appears in the output; a test invoking the CLI without `--vision-model` asserts argparse rejects it (exit code 2) before any pipeline stage runs.

#### FR-7.2: End-to-end orchestration
`cli.py` wires `input_resolver` → `osm_lookup` → (conditionally, per FR-4.5) `gmaps_scraper` + `photo_classifier` → `decision_engine` → `storage` + stdout output, propagating any `error` from an earlier stage straight to the final output without invoking later stages.
**Verify:** An end-to-end test with all external calls (Overpass, Playwright, Anthropic) mocked runs the full CLI for one OSM-positive case and one GMaps-fallback case, asserting the correct output for each.

### Architectural Requirements

#### AR-7.1: Configuration invariants
CLI numeric flags are validated at startup, before any pipeline stage runs: `osm_radius > 0`, `osm_timeout > 0`, `page_timeout > 0`, `max_photos >= 1`, and `1 <= threshold <= max_photos`. A violation produces a standard argparse error and exit code 2.

#### AR-7.2: Exit codes and stream discipline
Once the pipeline runs, exactly one JSON document (Feature 5) is written to stdout; all logs/diagnostics go to stderr. Exit code is **0** whenever the CLI produces a well-formed JSON result — whether that result is a successful `label` or a defined `error` code — since both are contractually valid, parseable outcomes. Exit code is **1** only for a truly unhandled internal failure, in which case the CLI still attempts to print the normal FR-5.1 envelope — `input` preserved (it's always known, even on internal failure), `evidence: []`, `resolved`/`label`/`method_used`/`confidence` all `null`, and `error: {"code": "INTERNAL_ERROR", "message": "..."}` — rather than a schema-breaking shortcut. Exit code **2** is reserved for CLI usage/validation errors raised by argparse or by AR-7.1's invariant checks — these happen *before* the pipeline runs, so no JSON is emitted for them; this is the one exception to "exactly one JSON document," since there is no pipeline run to report on.

---

## Feature 8: Automated Test Suite

**Who & why:** The GMaps-scraping layer is inherently fragile (unofficial DOM, no ToS-sanctioned API) and will need updates over time; the rest of the pipeline (resolution, OSM logic, threshold logic, error handling) must stay verifiably correct through those changes without needing live network/browser/API access on every run.

### Functional Requirements

#### FR-8.1: Ship a pytest suite with mocked externals
A `tests/` suite, runnable via `pytest`, covers every FR's **Verify** condition above using mocks/fakes for Overpass HTTP calls, the Playwright browser/page objects, and the Anthropic client — the default `pytest` run makes no real network call. Tests that exercise real Overpass/Maps calls are marked `@pytest.mark.integration` and are excluded by default.
**Verify:** `pytest` run in an offline sandbox (no network access) passes completely; `pyproject.toml` configures `-m "not integration"` as the default test selection.

---

## Data Requirements

- **`ResolvedPOI`**: `lat: float`, `lng: float`, `name: str | None`, `maps_url: str`.
- **`Photo`**: `bytes: bytes`, `mime_type: str`, `source_url: str | None` — carries provenance from `gmaps_scraper` through `photo_classifier` to `storage`, so evidence sidecars can record where a photo came from.
- **`ClassificationResult`**: `is_playground: bool`, `confidence: float` (required, `[0, 1]`) — only produced for a classification call that completed; a failed call (FR-4.4) skips the photo instead of producing a result with a null confidence.
- **CLI JSON output** (Feature 5): `input: str`, `resolved: {lat, lng, name, maps_url} | null`, `label: "playground nearby" | "no playground nearby" | null`, `method_used: "osm" | "gmaps_photos" | null`, `confidence: float | null`, `evidence: list[str]`, `error: {code: str, message: str} | null`.
- **Error codes** (values of `error.code`): `INVALID_INPUT`, `POI_NOT_FOUND`, `NO_PHOTOS_AVAILABLE`, `SCRAPE_BLOCKED`, `TIMEOUT`, `CLASSIFIER_ERROR`, `CONFIG_ERROR`, `INTERNAL_ERROR`.
- **Evidence metadata sidecar** (Feature 6): POI id/coordinates, photo source URL, timestamp, classifier name/model, confidence.

## Integration Points

- **OpenStreetMap Overpass API** (default `overpass-api.de`, overridable via `--osm-endpoint`) — public, unauthenticated, rate-limited by fair use; send a descriptive `User-Agent` header. Verified query syntax (including the `nwr` combinator and `out count;`) per the [Overpass API/Overpass QL wiki](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL).
- **Anthropic Messages API** — requires `ANTHROPIC_API_KEY`; image content blocks per [Vision - Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/vision): JPEG/PNG/GIF/WebP, **10MB max base64-encoded payload** on the direct API, **8000×8000px** max dimensions (re-verified directly against current docs during spec update — the original 20MB figure in v1 of this spec was incorrect).
- **Google Maps web UI via Playwright/Chromium** — unofficial, no auth, no ToS-sanctioned access; see AR-3.1 and the Constraints section below.

## Related Specs

None — this is the first spec in this repository.

## Constraints
- Python **3.12** minimum.
- **Poetry** for dependency management and packaging. Direct dependencies: an HTTP client (e.g. `httpx`), Playwright (plus its browser binary, provisioned via a `playwright install chromium` step documented in the project's setup instructions — a `poetry install` alone does not provision the browser), the Anthropic Python SDK, and an image-processing library (e.g. Pillow) for resize/validation ahead of the 10MB/8000×8000 limits in FR-4.2.
- **argparse** (stdlib) for the CLI — no additional CLI framework dependency.
- No Google Maps API account. Google Maps access is via Playwright browser automation only. This is against Google's Terms of Service; accepted as a deliberate, low-volume, personal/experimental-use risk. Revisit before any scaling, public deployment, or productization.
- Low expected call volume — no dedicated rate-limiter/queueing system is required; per-step timeouts (Feature 2, 3) are sufficient.
- The module boundaries in Features 1–6 (`input_resolver`, `osm_lookup`, `gmaps_scraper`, `photo_classifier`, `decision_engine`, `storage`) must be preserved as independently importable units so the pipeline can later be reused as a backend service or app without rewriting core logic.

## Out of Scope
- Route-level multi-POI candidate search (finding stops along a route) — future spec, consumes this classifier.
- Google Places API integration — revisit only if an API account becomes available.
- Training or shipping a specialized/local image classifier — only the pluggable interface (FR-4.1) is required now.
- Batch mode (classifying multiple POIs in a single invocation).
- Any GUI or web frontend — CLI only.
- Caching or persisting OSM/Maps results across separate runs.

## Assumptions & Risks
- **The label is a heuristic, not a proof.** An OSM miss is treated as inconclusive (FR-2.3), but a negative from the photo path means only "no playground visible in this POI's sampled photos" — it does not prove no playground exists nearby (e.g. it could be behind the POI, outside the photographed area, or simply not photographed by any Maps contributor). False negatives are expected and accepted for v1, consistent with the original requirement for a simple binary label; this is a known limitation, not a defect.
- **Google Maps' DOM is unofficial and will drift.** AR-3.1 isolates selectors for maintainability, but scraper breakage after a Google UI change is expected over time, not a one-time risk.
- **The public Overpass instance offers no uptime guarantee.** FR-2.3's fallback and the `--osm-endpoint` override (FR-7.1) mitigate but don't eliminate this.
- **Evidence photos are third-party user-contributed content** (uploaded to Google Maps by other users) and may depict identifiable people. No additional rights/licensing review is performed before persisting them locally in v1 — consistent with the already-accepted personal/experimental-use posture (see Constraints). Revisit before any sharing, publication, or use as training data beyond personal experimentation.

## Spec Completeness Checklist

- [x] **Scope & acceptance criteria** — single-POI scope stated in Overview/Goals; every FR has a Verify line; Out of Scope enumerates explicit non-goals.
- [x] **Testing strategy** — Feature 8 (FR-8.1) requires a `pytest` suite covering every FR's Verify condition with externals mocked, plus opt-in integration tests.
- [N/A] **Existing patterns** — repository is empty (no prior code); confirmed via direct inspection, nothing to compare against.
- [x] **Dependencies** — Constraints now lists each direct dependency (HTTP client, Playwright + browser binary provisioning, Anthropic SDK, image library) with its justifying requirement.
- [x] **Architecture & interfaces** — module boundaries and ownership are defined in each Feature's AR (AR-1.1 now also covers browser-session ownership; AR-2.1; AR-3.1/3.2; AR-4.1/4.2; AR-7.1/7.2), and Data Requirements defines all shared value types including the newly-added `Photo` type.
- [x] **Error handling & failure modes** — full error taxonomy (`INVALID_INPUT`, `POI_NOT_FOUND`, `NO_PHOTOS_AVAILABLE`, `SCRAPE_BLOCKED`, `TIMEOUT`, `CLASSIFIER_ERROR`, `CONFIG_ERROR`, `INTERNAL_ERROR`) is assigned to the FR of the stage that raises it, with a discriminated success/error output schema (FR-5.1) and exit-code contract (AR-7.2).
- [x] **Security review** — AR-4.1 mandates the Anthropic key come only from an environment variable and never be logged; Assumptions & Risks discloses the photo-provenance/PII posture explicitly rather than claiming no PII is involved.
- [x] **Performance impact** — OSM fast-path (Feature 2), the photo-count cap plus early-stop threshold with dedup (FR-3.2, FR-4.3), and explicit client+server timeouts (FR-2.3, FR-3.1) bound latency, bandwidth, and Claude API cost per run.
- [N/A] **Rollout & migration** — greenfield CLI tool with no existing users/data to migrate.
- [x] **Assumptions & risks** — dedicated Assumptions & Risks section states the heuristic nature of the label, scraper fragility, Overpass reliability, and evidence-photo provenance explicitly.

---

## Change Log

### Update from critique-consolidated-v-1.md

**Applied:**
- Corrected Anthropic image limit from an incorrect 20MB to the verified 10MB base64-encoded / 8000×8000px max (FR-4.2, Integration Points).
- Resolved the `INVALID_INPUT`/`POI_NOT_FOUND` self-contradiction with concrete, disjoint rules, including coordinate range validation and Maps-URL canonicalization precedence (FR-1.1–1.3).
- Split the output contract into an explicit success/error schema with nullable-field rules and a structured `error: {code, message}` object (FR-5.1).
- Fixed the evidence-persistence contradiction for `threshold > 1` by staging positive photos in memory until the final decision (FR-6.1), and added explicit handling for evidence write failures (FR-6.3).
- Made `--vision-model` a required flag with no default, resolving its conflict with `--help`'s "show every default" requirement (FR-7.1, FR-4.2) — per your explicit choice.
- Specified the photo-acquisition algorithm precisely: prefer direct URL extraction over thumbnail screenshots, dedup by URL/hash, define retrieval order and gallery-exhaustion condition (FR-3.2).
- Defined confidence aggregation for the GMaps-photos path: minimum confidence among qualifying photos when positive, `null` when negative (FR-4.3).
- Required Anthropic tool-use (forced tool choice) for structured classifier output instead of free-text parsing (AR-4.2).
- Clarified Playwright browser-session ownership between free-text resolution and scraping, including lazy creation and single-context lifecycle (AR-1.1).
- Fixed the Overpass query to include `relation`-tagged playgrounds via `nwr`, switched to `out count;` since geometry was never used, and separated server-side vs. client-side timeout handling (FR-2.1, FR-2.3).
- Added `CONFIG_ERROR` (fail fast on missing `ANTHROPIC_API_KEY` before scraping, FR-4.5) and `INTERNAL_ERROR` (catch-all, AR-7.2) to the error taxonomy.
- Added CLI numeric invariants (AR-7.1) and an exit-code/stream-discipline contract (AR-7.2).
- Added a `Photo` value type carrying MIME type and source URL/provenance (Data Requirements).
- Added an explicit dependency list and Playwright browser-binary provisioning step (Constraints).
- Added a dedicated Assumptions & Risks section disclosing the heuristic nature of the binary label, scraper fragility, Overpass reliability, and evidence-photo provenance — without changing the binary output contract itself (see Rejected).
- Added `--osm-endpoint` as an overridable flag (resilience against the single public Overpass instance).
- Non-blocking fixes: URL-encoding free-text search input (FR-1.2), output-directory slug rule (FR-6.1), `--page-timeout` now explicitly bounds Playwright's default navigation/action timeouts rather than relying on Playwright's own separate defaults (FR-3.1).

**Rejected:**
- Replacing the binary `label` with a third "unknown"/"no evidence" state — this would change the original, explicitly-requested output contract (a simple has/doesn't-have label) beyond what the critique or you asked for. Addressed instead via disclosure in the new Assumptions & Risks section, which is the right scope for a known heuristic limitation.
- Enumerating a long tail of additional error codes (e.g. separate `STORAGE_ERROR`, `BROWSER_ERROR`, `IMAGE_ERROR`) — kept the taxonomy lean: storage failures are handled behaviorally without a new code (FR-6.3), and `INTERNAL_ERROR` covers truly unanticipated failures, per the "simplicity over completeness" principle.

**Reorganized:**
- None — all changes were applied in place within their existing Features; no Features were added, removed, or reordered.

### Update from critique-consolidated-v-2.md

**Applied:**
- Fixed FR-1.3's Verify line, which contradicted its own rule (expected gibberish free text to return `INVALID_INPUT` when the rule itself routes it to `POI_NOT_FOUND`). While fixing this, simplified the `INVALID_INPUT`/`POI_NOT_FOUND` split to two clean, non-overlapping buckets: `INVALID_INPUT` for input that's malformed *before any resolution is attempted* (empty/whitespace, out-of-range coordinates); `POI_NOT_FOUND` for anything that's well-formed but resolves to nothing after FR-1.2's steps are attempted.
- Unified the Maps-URL/short-link/free-text resolution mechanism (FR-1.2): explicit destination coordinates in a URL are used directly (no browser); everything else without explicit coordinates — place ID, query string, or free text — is resolved by navigating/searching it in the shared Playwright context and reading back the result. This makes the previously-unexecutable place-ID branch concrete, and as a side effect gives resolution-stage browser operations the same `--page-timeout`-derived timeout as scraping, closing the previously-undefined "TIMEOUT during resolution" gap referenced by FR-5.1. The short-link HTTP redirect fetch gets its own fixed 5-second timeout, stated explicitly in AR-1.1.
- Made browser ownership single-sourced: AR-1.1 now states `cli.py` is the sole owner of the Playwright context and that no other module calls its launch/close APIs; FR-3.1 now says `gmaps_scraper` only ever *receives* that context, removing the wording that let it appear to launch its own.
- Made `ClassificationResult.confidence` a required `float` instead of `float | None` (FR-4.1, Data Requirements) — a failed classification now skips the photo entirely (FR-4.4) rather than producing a result with a null confidence, which removes the undefined `min()`-over-`None` case from FR-4.3's aggregation.
- Added an explicit exit-code-2 carve-out to AR-7.2 for pre-pipeline CLI usage/validation errors (argparse, AR-7.1), reconciling it with AR-7.1's existing exit-2 behavior.
- Non-blocking: added a note to FR-2.1 on the actual shape of Overpass's `out count;` response (`elements[0].tags.total`), confirmed via a live query against the production endpoint during critique; noted Anthropic's `strict: true` tool-use option in AR-4.2; tightened a cross-reference in FR-6.1's wording.

**Rejected:**
- None — every item in critique-consolidated-v-2.md was a surgical, well-scoped fix; nothing suggested added scope or unnecessary complexity.

**Reorganized:**
- None — all changes were applied in place within their existing Features.

### Update from critique-consolidated-v-3.md

**Applied:**
- **Restored Feature 8 (Automated Test Suite / FR-8.1)**, which had been silently dropped during the v1→v2 rewrite despite the completeness checklist still claiming it existed — this was the most severe finding of any round, since it left the spec without its mandatory testing requirement. Restored verbatim from the original v1 text.
- Fixed FR-1.2 to stop treating a URL's `@lat,lng` viewport segment as automatically authoritative: it now prefers the `data=` parameter's `!3d/!4d` pair (the POI's actual coordinate) when present, falling back to `@lat,lng` only otherwise — protects the 150m-radius OSM check (FR-2.1) from silent corruption by a stale/panned viewport.
- Closed the `ResolvedPOI.maps_url` gap: bare coordinate input now gets a synthesized canonical Maps query URL, so `maps_url` is always populated as its mandatory `str` type requires; removed FR-3.1's now-dead "no direct URL exists" branch.
- Fixed FR-4.5's wording, which claimed no Playwright work occurs on a missing API key — untrue whenever resolution itself needed the browser (free text, place-ID URLs). Now matches its own (correct) Verify line: no *further* Playwright work / no `gmaps_scraper` invocation.
- Fixed AR-7.2's `INTERNAL_ERROR` fallback, which specified "other fields null," contradicting FR-5.1's schema (`input` always present, `evidence: []`). Now follows the normal FR-5.1 envelope.
- Updated the Spec Completeness Checklist's "Testing strategy" line, now accurate again against the restored Feature 8.

**Rejected:**
- None.

**Reorganized:**
- None — Feature 8 was restored in its original position (after Feature 7, before Data Requirements); all other fixes were applied in place.

### Update from critique-consolidated-v-4.md

**Applied:**
- Closed the last gap in the resolution-stage `TIMEOUT` story: FR-1.3 now has a third bullet stating that exceeding the shared browser context's timeout or the short-link HTTP client's 5-second timeout during resolution returns `error: TIMEOUT`, with a matching Verify case — previously FR-5.1 referenced this outcome without any FR in Feature 1 actually creating it (AR-1.1 only ever defined the timeout *values*, not the resulting error).
- Non-blocking: aligned AR-1.1's wording ("a URL with explicit destination coordinates") with FR-1.2's more precise `!3d/!4d`-then-`@lat,lng` precedence language, so both describe the browser-needed condition identically.
- Non-blocking: trimmed FR-1.3's `POI_NOT_FOUND` definition, removing the now-redundant "or a URL with no extractable destination at all" clause — that case is already covered by "a browser navigation/search that finds nothing" under FR-1.2's current unified resolution mechanism.

**Rejected:**
- None.

**Reorganized:**
- None — all changes applied in place within Feature 1.
