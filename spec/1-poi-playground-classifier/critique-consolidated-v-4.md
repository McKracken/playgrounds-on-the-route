# Spec 1: Consolidated Critique (v4)

## Overview
**Critiques received from:** Claude (direct), Codex (two attempts — the first spent its time re-reading the file via shell commands and didn't finish; a second, narrowly-scoped attempt with the relevant text quoted inline converged immediately)
**Critiques missing:** Gemini — still not installed on this machine.

## Resolution Status of v3's 5 Blocking Items
**All 5 are confirmed RESOLVED**, verified by direct re-reading of the actual spec text (not the Change Log's claims):
1. Feature 8 / FR-8.1 (the testing requirement) is genuinely back in the file.
2. FR-1.2 now prefers the `!3d/!4d` data-parameter coordinate over the `@lat,lng` viewport.
3. `ResolvedPOI.maps_url` is always populated (synthesized for bare coordinates); FR-3.1's dead branch is gone.
4. FR-4.5's wording now matches its own Verify line.
5. AR-7.2's `INTERNAL_ERROR` fallback now follows FR-5.1's actual schema.

This round's structural cross-check (every AR the checklist cites, every error code the enum lists) confirmed everything else lines up — no other structural drift like v3's dropped Feature 8.

## Executive Summary
One new, genuinely blocking gap, found independently by both critiques via two different methods (Claude's full structural cross-check; Codex's isolated textual analysis of the quoted passages): **FR-5.1 references a "TIMEOUT during resolution" outcome that no requirement in Feature 1 actually creates.** AR-1.1 defines the timeout *values* for resolution-stage operations, but FR-1.3 — the only place Feature 1 defines its error outcomes — lists just `INVALID_INPUT` and `POI_NOT_FOUND`. This is the same class of issue as v2's originally-flagged "undefined resolution-stage TIMEOUT," which I'd marked resolved after defining the timeout *values* in AR-1.1 without noticing I'd never added the corresponding *rule* connecting "timeout exceeded" to "return `error: TIMEOUT`" — the same gap Feature 3 closes explicitly via FR-3.3 ("If any step exceeds the timeout configured in FR-3.1, return `error: TIMEOUT`").

Two small non-blocking wording items remain (a stale phrase in AR-1.1, a redundant clause in FR-1.3's `POI_NOT_FOUND` definition) — neither changes behavior.

## Blocking Items

1. **[CONFIRMED — both critiques, independently] Feature 1 never actually raises `TIMEOUT`, despite FR-5.1 assuming it does.** AR-1.1 defines resolution-stage timeout values (the shared browser context's `--page-timeout`-derived bound; a fixed 5-second short-link HTTP timeout) but no FR states that exceeding them returns `error: TIMEOUT`. FR-1.3 only defines `INVALID_INPUT` and `POI_NOT_FOUND`. **Fix:** add a third bullet to FR-1.3 mirroring FR-3.3's pattern — e.g. "**`TIMEOUT`**: the shared browser context's timeout, or the short-link HTTP client's 5-second timeout (both defined in AR-1.1), is exceeded during resolution" — with a matching Verify case (e.g. a mocked hung navigation/HTTP call asserts `error: TIMEOUT` with `resolved=null`).

## Non-Blocking Improvements
- AR-1.1 still says "a URL with explicit destination coordinates" when describing when the browser is skipped; FR-1.2 now uses more precise language ("a full Maps URL with an extractable precise coordinate," covering the `!3d/!4d` vs. `@lat,lng` distinction). Same underlying condition, just worth aligning the phrasing.
- FR-1.3's `POI_NOT_FOUND` definition retains a now-redundant clause ("a URL with no extractable destination at all") that's effectively folded into "browser navigation/search that finds nothing" given FR-1.2's current unified resolution mechanism. Harmless, but could be trimmed for clarity.

## Response to Spec Completeness Checklist
"Error handling & failure modes" should move from `[x]` to `[ ]` pending the fix above — `TIMEOUT` is listed in the error taxonomy and is genuinely raised by Feature 3, but Feature 1's own path to it is currently just an assumption in FR-5.1, not a defined rule.
