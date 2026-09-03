# Spec 1: Consolidated Critique (v3)

## Overview
**Critiques received from:** Claude (direct), Codex (two attempts on the audit — the first ran long doing its own live grep/nl exploration and didn't finish writing a file; a second, differently-scoped attempt completed cleanly)
**Critiques missing:** Gemini — still not installed on this machine.

## Resolution Status of v2's 6 Blocking Items
**All 6 are confirmed RESOLVED**, independently verified by both critiques against the actual FR/AR text (not just the spec's own Change Log claims): FR-1.3's Verify contradiction, browser-ownership wording split, the unexecutable Maps-URL place-ID branch, nullable-confidence `min()` aggregation, the AR-7.1/AR-7.2 exit-code conflict, and the undefined resolution-stage `TIMEOUT`.

## Executive Summary
This round surfaced something more serious than any prior finding: **the mandatory unit-test requirement (Feature 8 / FR-8.1) was silently dropped** from the spec during the v1→v2 rewrite, while the Spec Completeness Checklist still falsely claims it exists and is satisfied. Both critiques found this independently. My own structural lint (FR-count == Verify-count) missed it because two other FRs were added in the same rewrite, masking the net change in the total count — a real gap in how I'd been verifying my own edits.

Beyond that, three smaller-but-real self-contradictions surfaced, all confirmed by direct re-reading of the spec text: an absolute claim in FR-4.5 that's false for browser-resolved inputs, an `INTERNAL_ERROR` fallback shape that contradicts FR-5.1's schema, and an unresolved question about what `ResolvedPOI.maps_url` contains for bare-coordinate input. I also independently found and verified (via web search) a substantive accuracy issue in how Google Maps URLs are parsed: the `@lat,lng` viewport coordinate is not the same as the POI's actual coordinate, and conflating them could silently corrupt the 150m-radius OSM check.

## Blocking Items

1. **[CONFIRMED — both critiques, most severe finding to date] Feature 8 (the mandatory `pytest`-suite requirement) no longer exists in the spec, but the completeness checklist still cites it as present and checked.** `grep -n "FR-8\|Feature 8"` against the current spec matches only the checklist's own dangling reference. This is a regression introduced when the entire file was rewritten via `Write` during the v1→v2 update — the testing Feature simply wasn't carried forward. Per the spec-write skill's own rules, every spec must ship an explicit testing requirement; right now this one doesn't, while asserting that it does. **Fix:** restore a Feature 8 (or an equivalent FR under Feature 7) requiring a `pytest` suite that covers every FR's Verify condition with externals (Overpass, Playwright, Anthropic) mocked, matching what the original v1 spec had — then re-verify the checklist line against the actual restored text.

2. **[CONFIRMED — Codex, independently re-verified by Claude] FR-4.5's absolute claim contradicts FR-1.2/AR-1.1.** FR-4.5 says a missing `ANTHROPIC_API_KEY` returns `CONFIG_ERROR` "without performing any Playwright work" — but free-text and place-ID-only URL inputs already require Playwright during resolution (FR-1.2/AR-1.1), which runs *before* `osm_lookup` (FR-7.2's pipeline order) and thus before FR-4.5's own check point. FR-4.5's own Verify line already states the narrower, correct claim ("`gmaps_scraper` is never invoked"). **Fix:** align the requirement text with its own Verify line — "without invoking `gmaps_scraper` or performing any further Playwright work" — rather than the current absolute claim.

3. **[CONFIRMED — Codex, independently re-verified by Claude] `AR-7.2`'s `INTERNAL_ERROR` fallback shape contradicts FR-5.1's output schema.** AR-7.2 says the fallback prints the error object "with other fields `null`," but FR-5.1 requires `input` to always be present (never null) and `evidence` to default to `[]`, not null. **Fix:** state that the `INTERNAL_ERROR` fallback follows the normal FR-5.1 envelope — raw `input` preserved, `evidence: []` — with only `resolved`/`label`/`method_used`/`confidence` set to `null`.

4. **[CONFIRMED — Codex, independently re-verified by Claude] `ResolvedPOI.maps_url` is typed as a mandatory `str` but has no defined value for bare-coordinate input.** FR-3.1 implies `maps_url` can be absent ("a `lat,lng` search query if no direct URL exists"), which contradicts Data Requirements' non-nullable typing, and no FR states what populates the field in that case. **Fix:** have `input_resolver` synthesize a canonical Maps search/query URL for bare coordinates (e.g. `https://www.google.com/maps?q=lat,lng`) so `maps_url` is always genuinely populated, and remove FR-3.1's "if no direct URL exists" branch as dead code once that's true.

5. **[CONFIRMED — Claude, verified via web search, not raised by Codex] FR-1.2 treats a URL's `@lat,lng` viewport coordinate as authoritative, but it isn't necessarily the POI's actual coordinate.** Google Maps URLs typically also carry the POI's precise coordinate separately in the `data=` parameter as `!3d<lat>!4d<lng>`; `@lat,lng` reflects the map's last pan/zoom state, which can drift from the actual place. Given the default OSM radius is only 150m, using the wrong coordinate source could silently flip an OSM hit/miss or misdirect the entire GMaps fallback. **Fix:** prefer `!3d/!4d` when present; fall back to `@lat,lng` only when it isn't.

## Non-Blocking Improvements
- FR-3.2's "normalized photo URL" remains undefined — low priority, safe to leave as an implementation detail.

## Response to Spec Completeness Checklist
The "Testing strategy" checklist item must move from `[x]` to `[ ]` until Blocking Item 1 is fixed — it is currently the one checklist claim in the entire spec that is factually false against the text it cites.
