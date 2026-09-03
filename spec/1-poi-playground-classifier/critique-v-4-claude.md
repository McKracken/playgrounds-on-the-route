# Spec 1: POI Playground Classifier (CLI) — Claude Critique (v4)

Re-read the full current `spec.md` end to end, specifically re-checking structural claims against actual content this time (the lesson from v3's dropped-Feature-8 finding), plus a fresh self-consistency pass.

## Verification of v3's 5 Blocking Items

All five are **RESOLVED**, confirmed by direct re-reading (not by trusting the Change Log's narrative):

1. **Feature 8 / FR-8.1 exists in the actual file** (lines 200–208, confirmed via direct read, not just grep) — the dropped testing requirement is genuinely back, and the checklist's "Testing strategy" citation of "Feature 8 (FR-8.1)" now matches real content.
2. FR-1.2 now explicitly prefers the `data=` parameter's `!3d/!4d` pair over the `@lat,lng` viewport, with the viewport demoted to a fallback ("used only when no `!3d/!4d` pair is present").
3. `ResolvedPOI.maps_url` is now always populated — bare coordinates get a synthesized `https://www.google.com/maps?q=<lat>,<lng>` URL, and FR-3.1's dead "no direct URL exists" branch is gone.
4. FR-4.5 now says "without invoking `gmaps_scraper` or performing any further Playwright work," matching its own Verify line, with an explicit parenthetical acknowledging resolution may already have used the browser.
5. AR-7.2's `INTERNAL_ERROR` fallback now follows the normal FR-5.1 envelope (`input` preserved, `evidence: []`) instead of the schema-breaking "other fields null."

**Structural cross-check** (learning from v3): I independently verified every AR number the completeness checklist cites (AR-1.1, AR-2.1, AR-3.1/3.2, AR-4.1/4.2, AR-7.1/7.2) actually exists in the file, and every error code in the Data Requirements enum (`INVALID_INPUT`, `POI_NOT_FOUND`, `NO_PHOTOS_AVAILABLE`, `SCRAPE_BLOCKED`, `TIMEOUT`, `CLASSIFIER_ERROR`, `CONFIG_ERROR`, `INTERNAL_ERROR`) is actually raised somewhere — which is how I found the one new issue below.

## New Finding (v4)

### Blocking

1. **[verified] `TIMEOUT` during resolution is referenced by FR-5.1 but never actually created by any Feature 1 requirement.** FR-5.1 says `resolved` is null for, among other things, "a `TIMEOUT` that occurs during resolution itself (e.g. a hung free-text search)." AR-1.1 defines the *values* for resolution-stage timeouts (the shared browser context's `--page-timeout`-derived bound; a fixed 5-second timeout for the short-link HTTP fetch), but no requirement in Feature 1 states the *rule* that exceeding either of those actually produces `error: TIMEOUT`. FR-1.3 only enumerates two outcomes — `INVALID_INPUT` and `POI_NOT_FOUND` — neither of which is a timeout. Compare Feature 3, where FR-3.3 explicitly closes this loop ("If any step exceeds the timeout configured in FR-3.1, return `error: TIMEOUT`") — Feature 1 has the timeout *values* (AR-1.1) but is missing the equivalent closing statement. This is the same class of gap v2's critique originally flagged and I believed I'd fully closed "as a side effect" of unifying the resolution mechanism — I had only fixed what the timeout values are, not that exceeding them actually raises the error FR-5.1 already assumes exists. **Fix:** add a third bullet to FR-1.3 — e.g. "**`TIMEOUT`**: the shared browser context's navigation/action timeout, or the short-link HTTP client's 5-second timeout (both defined in AR-1.1), is exceeded during resolution" — with a matching Verify case.

### Non-Blocking
- AR-1.1's condition for when the browser is needed still uses the pre-v3 phrase "a URL with explicit destination coordinates," while FR-1.2 now uses more precise language ("a full Maps URL with an extractable precise coordinate," covering the `!3d/!4d`-vs-`@lat,lng` distinction). Both describe the same underlying condition, so this isn't a contradiction — just a small terminology mismatch worth tightening for a reader scanning AR-1.1 in isolation.
- FR-1.3's `POI_NOT_FOUND` definition still includes the clause "or a URL with no extractable destination at all," which is now effectively unreachable as a distinct case since every such URL is routed to browser navigation (FR-1.2) and would surface as "browser navigation/search that finds nothing" instead — harmless redundancy, not a contradiction.
