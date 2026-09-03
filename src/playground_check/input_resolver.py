"""POI input resolution (spec Feature 1: FR-1.1, FR-1.2, FR-1.3, AR-1.1).

Classifies whatever form of Maps input the user handed the CLI -- a full
Google Maps URL, a `maps.app.goo.gl`/`goo.gl/maps` short link, a bare
`lat,lng` coordinate pair, or free-text place name/address -- and resolves it
to a canonical `ResolvedPOI`, escalating from the cheapest mechanism (regex
extraction) to the most expensive (a real Playwright browser navigation)
only when a cheaper one can't produce a precise coordinate.

This module never creates or closes the shared Playwright `BrowserContext`
itself (AR-1.1) -- `cli.py` owns its lifecycle. `resolve()` only ever calls
the `get_context` factory it's handed, and only on the one resolution path
that actually needs a browser.
"""

from __future__ import annotations

import math
import re
import urllib.parse
from collections.abc import Callable

import httpx
from playwright.sync_api import BrowserContext
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from playground_check.errors import InvalidInputError, OperationTimeoutError, PoiNotFoundError
from playground_check.models import ResolvedPOI

#: FR-1.1's syntactic coordinate-pair pattern.
_COORD_RE = re.compile(r"^-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?$")

#: Google Maps short-link hosts (FR-1.1).
_SHORT_LINK_RE = re.compile(r"(maps\.app\.goo\.gl|goo\.gl/maps)", re.IGNORECASE)

#: Anything else that looks like a URL is treated as a "full Google Maps URL"
#: candidate (FR-1.1's third bucket); short links are carved out above first
#: since they'd otherwise also match this.
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

#: The `data=` parameter's `!3d<lat>!4d<lng>` pair -- the POI's actual
#: location (FR-1.2, precision source 1).
_DATA_COORD_RE = re.compile(r"!3d(-?[\d.]+)!4d(-?[\d.]+)")

#: The `@lat,lng` viewport/pan segment -- only the map's last pan/zoom
#: center, so a fallback and never preferred over `_DATA_COORD_RE`
#: (FR-1.2, precision source 2).
_VIEWPORT_COORD_RE = re.compile(r"@(-?[\d.]+),(-?[\d.]+)")

#: Best-effort place name straight out of a `/maps/place/<name>/...` URL path.
_PLACE_NAME_RE = re.compile(r"/maps/place/([^/@]+)")

#: Fixed per FR-1.2/AR-1.1: short-link redirect resolution is "a single
#: lightweight HTTP call, not a full page load" -- not configurable.
_SHORT_LINK_HTTP_TIMEOUT = 5.0

#: Suffixes Google Maps commonly appends to the page `<title>`; stripped
#: for a cleaner best-effort `name` (FR-1.2).
_TITLE_SUFFIXES = (" - Google Maps", " - Google My Maps")


def resolve(raw_input: str, get_context: Callable[[], BrowserContext]) -> ResolvedPOI:
    """Classify and resolve `raw_input` to a canonical `ResolvedPOI`.

    Raises `InvalidInputError`, `PoiNotFoundError`, or `OperationTimeoutError`
    (spec FR-1.3) when resolution can't produce one.
    """
    stripped = raw_input.strip()
    if not stripped:
        raise InvalidInputError("Input is empty or whitespace-only.")

    if _COORD_RE.match(stripped):
        return _resolve_coordinate(stripped)
    if _SHORT_LINK_RE.search(stripped):
        return _resolve_short_link(stripped, get_context)
    if _URL_RE.match(stripped):
        return _resolve_full_url(stripped, get_context)
    return _resolve_via_browser(stripped, get_context, is_free_text=True)


def _resolve_coordinate(stripped: str) -> ResolvedPOI:
    """Bare `lat,lng` pair: validate then build directly, no browser (FR-1.2)."""
    lat_str, lng_str = (part.strip() for part in stripped.split(",", 1))
    try:
        lat = float(lat_str)
        lng = float(lng_str)
    except ValueError as exc:
        raise InvalidInputError(f"Could not parse coordinate pair: {stripped!r}") from exc

    if not math.isfinite(lat) or not (-90.0 <= lat <= 90.0):
        raise InvalidInputError(f"Latitude out of range or non-finite: {lat_str!r}")
    if not math.isfinite(lng) or not (-180.0 <= lng <= 180.0):
        raise InvalidInputError(f"Longitude out of range or non-finite: {lng_str!r}")

    return ResolvedPOI(
        lat=lat,
        lng=lng,
        name=None,
        maps_url=f"https://www.google.com/maps?q={lat},{lng}",
    )


def _resolve_full_url(url: str, get_context: Callable[[], BrowserContext]) -> ResolvedPOI:
    """A full Maps URL: use an extractable precise coordinate directly, or
    fall back to the browser (FR-1.2)."""
    coord = _extract_precise_coord(url)
    if coord is not None:
        lat, lng = coord
        return ResolvedPOI(lat=lat, lng=lng, name=_extract_name_from_url(url), maps_url=url)
    return _resolve_via_browser(url, get_context, is_free_text=False)


def _resolve_short_link(short_url: str, get_context: Callable[[], BrowserContext]) -> ResolvedPOI:
    """Follow the short link's HTTP redirect (no browser, no JS), then apply
    the same precise-coordinate extraction to the resolved URL (FR-1.2)."""
    try:
        response = httpx.get(short_url, follow_redirects=True, timeout=_SHORT_LINK_HTTP_TIMEOUT)
    except httpx.TimeoutException as exc:
        raise OperationTimeoutError(
            f"Timed out following short link redirect: {short_url!r}"
        ) from exc

    resolved_url = str(response.url)
    return _resolve_full_url(resolved_url, get_context)


def _resolve_via_browser(
    target: str,
    get_context: Callable[[], BrowserContext],
    *,
    is_free_text: bool,
) -> ResolvedPOI:
    """The one resolution path that needs a browser (FR-1.2): navigate to a
    URL directly, or submit free text as a Maps search, then read the
    resolved coordinate back from the page's own canonical `page.url`."""
    context = get_context()
    page = context.new_page()

    try:
        if is_free_text:
            page.goto(f"https://www.google.com/maps/search/{urllib.parse.quote(target)}")
        else:
            page.goto(target)
    except PlaywrightTimeoutError as exc:
        raise OperationTimeoutError(
            f"Timed out resolving POI via browser navigation for: {target!r}"
        ) from exc

    final_url = page.url
    coord = _extract_precise_coord(final_url)
    if coord is None:
        raise PoiNotFoundError(f"No Maps result could be resolved for: {target!r}")

    lat, lng = coord
    try:
        name = _clean_page_title(page.title())
    except Exception:  # noqa: BLE001 - name extraction is best-effort only
        name = None

    return ResolvedPOI(lat=lat, lng=lng, name=name, maps_url=final_url)


def _extract_precise_coord(url: str) -> tuple[float, float] | None:
    """FR-1.2's precision order: `!3d/!4d` (the POI's actual location) before
    `@lat,lng` (just the last pan/zoom center)."""
    match = _DATA_COORD_RE.search(url)
    if match is None:
        match = _VIEWPORT_COORD_RE.search(url)
    if match is None:
        return None
    return float(match.group(1)), float(match.group(2))


def _extract_name_from_url(url: str) -> str | None:
    """Best-effort `name` straight out of a `/maps/place/<name>/...` URL path."""
    match = _PLACE_NAME_RE.search(url)
    if match is None:
        return None
    name = urllib.parse.unquote_plus(match.group(1)).strip()
    return name or None


def _clean_page_title(title: str | None) -> str | None:
    """Best-effort `name` from the resolved page's `<title>`, with Maps'
    common trailing suffix stripped off."""
    if not title:
        return None
    cleaned = title.strip()
    for suffix in _TITLE_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    return cleaned or None
