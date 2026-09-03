# playground-check

Classify a Google Maps Point of Interest (POI) as having a nearby kid's
playground or not. Checks OpenStreetMap first (free, fast); if that's
inconclusive, falls back to opening the POI in Google Maps via a headless
browser, pulling photos, and classifying them with a vision model (any
[litellm](https://docs.litellm.ai/docs/providers)-supported provider —
Anthropic, OpenAI, Gemini, etc.).

See [`spec/1-poi-playground-classifier/spec.md`](spec/1-poi-playground-classifier/spec.md) for the full specification.

## Setup

```bash
poetry install
poetry run playwright install chromium
cp .env.example .env   # then fill in your provider's API key
```

Edit `.env` (gitignored, never committed) with the credential your chosen
`--vision-model` needs — e.g. `ANTHROPIC_API_KEY` for `anthropic/*` models,
`OPENAI_API_KEY` for `gpt-*` models — and, optionally,
`PLAYGROUND_CHECK_VISION_MODEL` so you don't have to pass `--vision-model`
on every invocation. `.env` is loaded automatically; a real shell-exported
variable always takes precedence over `.env`'s value. No credential is
needed at all when a POI's OSM check already confirms a hit — only the
Google Maps photo fallback path calls the vision model.

## Usage

```bash
poetry run playground-check "40.7308,-73.9973" --vision-model anthropic/claude-haiku-4-5
poetry run playground-check "https://maps.app.goo.gl/exampleShortLink" --vision-model gpt-4o
poetry run playground-check "Golden Gate Park, San Francisco"   # uses PLAYGROUND_CHECK_VISION_MODEL from .env
```

Prints one JSON document to stdout, e.g.:

```json
{"input": "40.7308,-73.9973", "resolved": {"lat": 40.7308, "lng": -73.9973, "name": null, "maps_url": "..."}, "label": "playground nearby", "method_used": "osm", "confidence": 1.0, "evidence": [], "error": null}
```

Run `poetry run playground-check --help` for all configurable flags
(radius, timeouts, photo cap, threshold, output paths).

## Development

```bash
poetry run pytest          # unit tests, no live network calls, offline by default
poetry run pytest -m integration   # also run tests that hit real Overpass/Google Maps
```

## Accepted risk

Google Maps has no public API for this use case, so photo retrieval uses
browser automation (Playwright) against the Google Maps web UI. This is
against Google's Terms of Service; it's accepted here as a deliberate,
low-volume, personal/experimental-use tradeoff — see the spec's
Constraints and Assumptions & Risks sections before scaling this beyond
that.
