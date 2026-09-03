# Spec 1: POI Playground Classifier (CLI) — Claude Critique (v3)

Re-reading the full current `spec.md` against v2's 6 blocking items, plus a fresh pass for new issues.

## Verification of v2's 6 Blocking Items

All six are **RESOLVED**, confirmed by direct re-reading:

1. FR-1.3's Verify line now correctly routes gibberish free text to `POI_NOT_FOUND` (line 34), consistent with its own rule.
2. AR-1.1 states `cli.py` is the sole owner of the Playwright context and no other module calls launch/close; FR-3.1 correspondingly only ever "receives" the context. Consistent.
3. FR-1.2 now unifies place-ID/query-text/free-text resolution through a single browser-navigation mechanism; the place-ID branch is now executable.
4. `ClassificationResult.confidence` is now a required `float`, both in FR-4.1 and Data Requirements — the `min()`-over-`None` case is gone.
5. AR-7.2 explicitly carves out exit code 2 for pre-pipeline argparse/AR-7.1 failures.
6. AR-1.1 now gives resolution-stage browser operations the same `--page-timeout` bound as scraping, and a fixed 5-second timeout for the short-link HTTP fetch — closing the gap FR-5.1 referenced.

## New Findings (v3)

### Blocking — most severe finding of any round so far

1. **[CONFIRMED — independently found before reading Codex's report] Feature 8 (the mandatory unit-test requirement) was silently dropped during the v1→v2 rewrite.** The original spec (v1) had a "Feature 8: Automated Test Suite" section with `FR-8.1` requiring a `pytest` suite. When I rewrote the entire file with a fresh `Write` call to apply v1's critique fixes, I did not carry Feature 8 forward — and the Spec Completeness Checklist's "Testing strategy" line still claims `[x]` and cites "Feature 8 (FR-8.1)" as if it exists. It doesn't: `grep -n "FR-8\|Feature 8"` against the current file matches only the checklist's own dangling reference. My own structural lint (`FR-count == Verify-count`) didn't catch this because I added two new FRs elsewhere (FR-4.5, FR-6.3) in the same rewrite, so the total count coincidentally still balanced (20 → 21, masking a −1/+2 net change). This is worse than any prior finding: per the spec-write skill's own rules, every spec **must** ship an explicit testing requirement, and right now this one doesn't — while its own checklist actively asserts otherwise. **Fix:** restore a Feature 8 (or fold an equivalent FR into Feature 7) requiring a `pytest` suite covering every FR's Verify condition with externals mocked, exactly as the original v1 spec had it, and re-verify the checklist claim against the restored text rather than trusting the change log's narrative.

### Blocking — independently confirmed, originally found by Codex

I re-verified each of these directly against the spec text myself; all three hold up:

2. **FR-4.5's "without performing any Playwright work" is false for any input that needed browser-based resolution.** FR-1.2/AR-1.1 already require Playwright for free text or any URL without explicit coordinates — and that resolution happens *before* `osm_lookup` even runs (FR-7.2's pipeline order). So by the time FR-4.5's check fires ("immediately after `osm_lookup` returns inconclusive"), Playwright may already have done work. The requirement's absolute claim and its own Verify line (which only asserts `gmaps_scraper` is never invoked — a narrower, correct claim) disagree with each other.
3. **`AR-7.2`'s `INTERNAL_ERROR` fallback ("other fields `null`") contradicts FR-5.1's schema**, which requires `input` to always be present (never null) and `evidence` to be `[]` rather than null when empty.
4. **`ResolvedPOI.maps_url` is typed as a mandatory `str` (Data Requirements) but FR-3.1 implies it can be absent** ("a `lat,lng` search query if no direct URL exists") for bare-coordinate input, with no stated rule for what actually populates the field in that case.

### New — not raised by Codex

5. **[verified via web search] `@lat,lng` in a Google Maps URL is the map *viewport center*, not necessarily the POI's exact coordinate — FR-1.2 treats it as authoritative without qualification.** Google's own URL format typically also embeds the POI's precise coordinate separately in the `data=` parameter as `!3d<lat>!4d<lng>` when a specific place is selected; the `@lat,lng` segment reflects wherever the map was panned/zoomed, which can drift from the actual place, especially for a link that was shared after some manual panning. FR-1.2 currently says "a full Maps URL with explicit destination coordinates in its path (e.g. `@lat,lng`) uses those coordinates directly." Given the default OSM radius is only **150 meters** (FR-2.1), a viewport/POI mismatch of even a modest amount could silently flip an OSM hit/miss, or feed the wrong coordinates into the GMaps fallback path entirely. **Fix:** when both are present, prefer the `data=` parameter's `!3d/!4d` coordinate over the `@lat,lng` viewport coordinate; fall back to `@lat,lng` only when no `!3d/!4d` pair is present.

### Non-Blocking (carried forward, still open, still low priority)
- FR-3.2's "normalized photo URL" remains undefined (e.g. whether Google's size/query parameters are stripped before comparison) — still fine to leave as an implementation detail given how much more concrete the rest of FR-3.2 already is.
