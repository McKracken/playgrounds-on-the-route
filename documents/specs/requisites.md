# Initial specification

## Goal & Overview
The goal is to have a software capable of classifying a Point of Interest (POI) from Google Maps into two classes: playground nearby / no playground nearby. By "playground" we mean a playground for kids.
The final reason is being able to find spots along a given route suitable for a stopover, to have the kids engaged while refuelling/eating/resting during a roadtrip.

This spec covers **single-POI classification only**. Route-level orchestration (taking a route and generating the list of candidate POIs to classify) is explicitly out of scope and will be a separate future spec that consumes this classifier.

## Inputs, Outputs & Data Contracts

### Input
A POI, in any of these forms (v1 must support all of them):
- A full Google Maps URL
- A Google Maps short link (`maps.app.goo.gl/...`)
- Raw coordinates (lat/lng)
- Free-text place name or address

All input forms are resolved to a canonical representation (place name, coordinates, and — if available — a Google Maps place id) before any further processing.

### Output
A JSON object (printed to stdout, optionally also written to a file via a CLI flag) containing:
- the resolved POI (name, coordinates, place id if available)
- `label`: `"playground nearby"` | `"no playground nearby"`
- `method_used`: `"osm"` | `"gmaps_photos"` (which path produced the label)
- `confidence` (when available from the classifier)
- `evidence`: list of saved photo paths/URLs that contributed to a positive label (empty for OSM-only / negative results)
- `error` (populated instead of a label when processing fails)

Evidence photos that are saved for display/check are written to a per-run output directory, each with a small metadata sidecar (POI id, source URL, timestamp, classifier + confidence) — this keeps the door open to reusing them later as training data for a specialized classifier, without committing to that now.

## Core Behavior & Logic

1. **Resolve input** to coordinates (+ place id/name if available), regardless of which input form was given.
2. **OSM first-pass (fast path):** query the OpenStreetMap Overpass API for nodes/ways tagged `leisure=playground` within a short radius (default **150 m**, configurable) of the resolved coordinates.
   - Hit found within radius → label immediately as **"playground nearby"**, `method_used = "osm"`, skip steps 3–5 entirely (no scraping, no LLM calls).
   - No hit, or Overpass unavailable/errors → treat as inconclusive and fall through to the photo-based path below. (OSM coverage is inconsistent outside dense urban areas, so "no hit" must never be treated as a final negative on its own.)
3. **Open the POI in Google Maps** via a simulated browser (Playwright), choosing the right entry method for the input form (open the URL directly, or submit the resolved query/coordinates through the Maps UI).
4. **Fetch photos** from the POI's photo gallery, up to a configured cap (default **20 photos**). If zero photos are available, return `error: NO_PHOTOS_AVAILABLE`.
5. **Classify each fetched photo** with an LLM vision call (Claude), asking whether the image shows kid-playground equipment (slides, swings, jungle gyms, etc.). Stop early once the configured threshold of positive photos is reached (default **1**, configurable) — no need to run through every photo.
6. **Label:** threshold met → `"playground nearby"`, `method_used = "gmaps_photos"`, save the qualifying photo(s) as evidence. Threshold not met after exhausting the photo cap → `"no playground nearby"`.

## Constraints & Guardrails
- No Google Maps API account. Google Maps is accessed via a simulated browser (**Playwright**), not an API. Call volume is expected to be low; use general precautions (timeouts, reasonable delays) rather than a full rate-limiting subsystem.
- Scraping Google Maps without using their API is against Google's Terms of Service. Accepted as a deliberate, low-volume, personal/experimental-use risk for now — this decision should be revisited before any scaling, public deployment, or productization.
- Photo classification does not need to examine every available photo — stop once the configured threshold of playground-positive photos is reached.
- CLI script for now (Python).
- Architecture must be modular, with clearly separated components, so it can later be reused as a backend service or app, and so the photo classifier specifically can be swapped for a specialized/local model later without touching the rest of the pipeline. Suggested module boundaries:
  - `input_resolver` — normalizes any supported input form to coordinates/place id
  - `osm_lookup` — Overpass query + radius check
  - `gmaps_scraper` — Playwright-driven navigation + photo extraction
  - `photo_classifier` — pluggable interface; v1 implementation calls Claude vision
  - `decision_engine` — threshold/early-exit logic combining OSM + photo results
  - `storage` — evidence photo + metadata persistence
  - `cli` — entrypoint wiring the above together

## Error Handling
Errors are returned in the output contract's `error` field (not just "no pictures"):
- `INVALID_INPUT` — input couldn't be parsed/resolved to a POI at all
- `POI_NOT_FOUND` — resolved input doesn't correspond to a real Maps location
- `NO_PHOTOS_AVAILABLE` — POI has zero photos to classify
- `SCRAPE_BLOCKED` — Google Maps blocked/challenged the automated browser (e.g. CAPTCHA)
- `TIMEOUT` — a step (Overpass query, page load, photo fetch) exceeded its timeout
- `CLASSIFIER_ERROR` — the vision classifier call failed

## Non-Goals (for this spec)
- Route-level candidate search / multi-POI orchestration (future spec)
- Google Places API integration (revisit only if an API account becomes available)
- Building/training a specialized local classifier (future work; today's evidence-photo storage is designed to make this possible later, not to implement it now)

## Decisions Confirmed
- Playground detection: **hybrid** — OSM Overpass short-radius lookup as a fast, free early-exit for clear positives; Google Maps photo classification as the fallback for everything else (no hit / inconclusive).
- Scope: single-POI classification only; route-level search deferred.
- Browser automation: **Playwright**.
- Photo classifier (v1): **LLM vision API (Claude)**.

## Open Defaults (proposed — confirm or adjust)
- OSM search radius: 150 m
- Max photos fetched per POI: 20
- Positive-classification threshold: 1 photo
