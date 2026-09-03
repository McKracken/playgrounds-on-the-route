"""CLI entrypoint wiring every module together (spec Feature 7: FR-7.1,
FR-7.2, AR-7.1, AR-7.2; plus FR-4.5, FR-5.1, FR-5.2, and AR-1.1's `cli.py`-
ownership half of the shared browser context).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext
from playwright.sync_api import sync_playwright

from playground_check import decision_engine, gmaps_scraper, input_resolver, osm_lookup, storage
from playground_check.errors import ConfigError, PlaygroundCheckError
from playground_check.models import ResolvedPOI
from playground_check.photo_classifier import ClaudeVisionClassifier

#: Default Overpass endpoint (spec FR-2.1).
_DEFAULT_OSM_ENDPOINT = "https://overpass-api.de/api/interpreter"

#: A realistic desktop user-agent/viewport for the shared browser context
#: (spec AR-3.2) -- not a real browser's exact string, just plausible.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_VIEWPORT = {"width": 1366, "height": 900}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="playground-check",
        description=(
            "Classify a Google Maps POI as having a nearby kid's playground "
            "or not."
        ),
    )
    parser.add_argument(
        "input",
        help=(
            "The POI: a full Google Maps URL, a maps.app.goo.gl/goo.gl/maps "
            "short link, a bare 'lat,lng' coordinate pair, or a free-text "
            "place name/address."
        ),
    )
    parser.add_argument(
        "--osm-radius",
        type=float,
        default=150,
        help="OSM Overpass search radius in meters (default: 150).",
    )
    parser.add_argument(
        "--osm-timeout",
        type=float,
        default=10,
        help="OSM Overpass query timeout in seconds (default: 10).",
    )
    parser.add_argument(
        "--osm-endpoint",
        type=str,
        default=_DEFAULT_OSM_ENDPOINT,
        help=f"Overpass API endpoint (default: {_DEFAULT_OSM_ENDPOINT}).",
    )
    parser.add_argument(
        "--max-photos",
        type=int,
        default=20,
        help="Maximum number of Google Maps photos to fetch (default: 20).",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=1,
        help="Number of positive photos needed to confirm a playground (default: 1).",
    )
    parser.add_argument(
        "--vision-model",
        type=str,
        required=True,
        help="Claude vision model to use for photo classification (required, no default).",
    )
    parser.add_argument(
        "--page-timeout",
        type=float,
        default=20,
        help="Browser navigation/action timeout in seconds (default: 20).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./output",
        help="Base directory for evidence photos (default: ./output).",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default=None,
        help="Optional path to additionally write the JSON result to.",
    )
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """AR-7.1's configuration invariants -- violations produce a standard
    argparse error (exit code 2) before any pipeline stage runs."""
    if args.osm_radius <= 0:
        parser.error("--osm-radius must be > 0")
    if args.osm_timeout <= 0:
        parser.error("--osm-timeout must be > 0")
    if args.page_timeout <= 0:
        parser.error("--page-timeout must be > 0")
    if args.max_photos < 1:
        parser.error("--max-photos must be >= 1")
    if not (1 <= args.threshold <= args.max_photos):
        parser.error("--threshold must satisfy 1 <= threshold <= max_photos")


def _resolved_dict(poi: ResolvedPOI) -> dict[str, Any]:
    return {"lat": poi.lat, "lng": poi.lng, "name": poi.name, "maps_url": poi.maps_url}


def _make_context_factory(page_timeout_seconds: float):
    """A lazy, memoizing `get_context: Callable[[], BrowserContext]` (spec
    AR-1.1): the Playwright/browser/context stack is created on first call
    and reused for the rest of this invocation; nothing is created if this
    is never called at all. Returns the factory plus a zero-arg `close()`
    that tears down whatever was actually created (safe to call even if the
    factory was never invoked)."""
    state: dict[str, Any] = {}

    def get_context() -> BrowserContext:
        if "context" not in state:
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_USER_AGENT, viewport=_VIEWPORT)
            context.set_default_timeout(page_timeout_seconds * 1000)
            context.set_default_navigation_timeout(page_timeout_seconds * 1000)
            state["playwright"] = playwright
            state["browser"] = browser
            state["context"] = context
        return state["context"]

    def close() -> None:
        if "context" in state:
            state["context"].close()
        if "browser" in state:
            state["browser"].close()
        if "playwright" in state:
            state["playwright"].stop()

    return get_context, close


def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Runs the pipeline (spec FR-7.2), returning the FR-5.1 JSON envelope.

    `resolved` is populated iff input resolution (Feature 1) itself
    succeeded -- tracked here via `poi`, regardless of which later stage (if
    any) subsequently fails -- matching FR-5.1's exact rule.
    """
    get_context, close_context = _make_context_factory(args.page_timeout)
    poi: ResolvedPOI | None = None

    try:
        poi = input_resolver.resolve(args.input, get_context)

        osm_hit = osm_lookup.check_nearby(
            poi.lat,
            poi.lng,
            radius=args.osm_radius,
            timeout=args.osm_timeout,
            endpoint=args.osm_endpoint,
        )

        if osm_hit:
            decision = decision_engine.decide_from_osm_hit()
        else:
            # FR-4.5: fail fast on a missing API key, before invoking
            # gmaps_scraper or doing any further Playwright work -- but
            # resolution (above) may already have used the browser.
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise ConfigError(
                    "ANTHROPIC_API_KEY is not set; cannot classify photos."
                )
            photos = gmaps_scraper.fetch_photos(poi, get_context, max_photos=args.max_photos)
            classifier = ClaudeVisionClassifier(args.vision_model)
            decision = decision_engine.decide_from_photos(
                photos, classifier, threshold=args.threshold
            )

        evidence = storage.save_evidence(decision, poi, Path(args.output_dir))

        return {
            "input": args.input,
            "resolved": _resolved_dict(poi),
            "label": decision.label,
            "method_used": decision.method_used,
            "confidence": decision.confidence,
            "evidence": evidence,
            "error": None,
        }
    except PlaygroundCheckError as exc:
        return {
            "input": args.input,
            "resolved": _resolved_dict(poi) if poi is not None else None,
            "label": None,
            "method_used": None,
            "confidence": None,
            "evidence": [],
            "error": {"code": exc.code.value, "message": exc.message},
        }
    except Exception as exc:  # noqa: BLE001 - AR-7.2's INTERNAL_ERROR catch-all
        return {
            "input": args.input,
            "resolved": _resolved_dict(poi) if poi is not None else None,
            "label": None,
            "method_used": None,
            "confidence": None,
            "evidence": [],
            "error": {"code": "INTERNAL_ERROR", "message": str(exc)},
        }
    finally:
        close_context()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)

    result = _run(args)

    # AR-7.2: exactly one JSON document to stdout; all logs/diagnostics
    # (there are none of our own here -- modules log their own warnings to
    # stderr) stay off stdout.
    print(json.dumps(result))
    if args.output_file:
        Path(args.output_file).write_text(json.dumps(result, indent=2))

    return 1 if result["error"] is not None and result["error"]["code"] == "INTERNAL_ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
