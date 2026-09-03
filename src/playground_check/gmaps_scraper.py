"""Google Maps photo-gallery retrieval via Playwright (spec Feature 3: FR-3.1,
FR-3.2, FR-3.3, AR-3.1, AR-3.2).

`gmaps_scraper` never launches or closes a browser/context itself -- per
AR-1.1, `cli.py` is the sole owner of the shared, memoized Playwright
``BrowserContext`` (created lazily, with its default navigation/action
timeout already set to ``--page-timeout``). This module only ever calls the
zero-arg ``get_context`` callable it's handed to obtain that same context.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from typing import Callable

import httpx
from playwright.sync_api import BrowserContext, Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from playground_check.errors import (
    NoPhotosAvailableError,
    OperationTimeoutError,
    ScrapeBlockedError,
)
from playground_check.models import Photo, ResolvedPOI

# ---------------------------------------------------------------------------
# AR-3.1: all Google Maps DOM selectors centralized here, isolated from the
# scraping logic below, for easy updates when Google changes markup.
#
# IMPORTANT / HONEST CAVEAT: Google Maps' DOM is unofficial, undocumented,
# and not available for live inspection in this sandboxed environment (no
# network/browser access). The strings below are a best-effort approximation
# based on publicly-documented patterns of Google Maps' web UI (ARIA labels
# on the photo/gallery controls, common consent/CAPTCHA interstitial
# markers) -- they are UNCONFIRMED against the live site and will likely need
# adjustment once someone can inspect real Google Maps markup. This is the
# accepted, documented risk called out in AR-3.1 and the spec's Assumptions &
# Risks section ("Google Maps' DOM is unofficial and will drift"), not a
# defect in this implementation.
# ---------------------------------------------------------------------------
SELECTORS: dict[str, str] = {
    # The main POI detail panel that FR-3.1 waits to render before proceeding.
    "poi_panel": "div[role='main']",
    # A CAPTCHA interstitial blocking automated access.
    "captcha_interstitial": (
        "iframe[src*='recaptcha'], "
        "iframe[title*='recaptcha' i], "
        "form#captcha-form, "
        "div.g-recaptcha"
    ),
    # A cookie-consent interstitial blocking the POI panel from rendering.
    "consent_interstitial": (
        "form[action*='consent'] button, "
        "div[aria-label*='consent' i], "
        "#CXQnmb"
    ),
    # The button/control on the POI panel that opens the full photo gallery.
    "photo_gallery_entry": (
        "button[aria-label*='Photos' i], "
        "button[jsaction*='heroHeaderImage']"
    ),
    # The opened photo-gallery/photo-viewer panel itself (waited on after
    # clicking the entry point -- distinct from individual items below, so a
    # gallery that opens but has zero items is NO_PHOTOS_AVAILABLE rather
    # than a TIMEOUT).
    "gallery_container": "div[aria-label*='Photos' i][role='region']",
    # Individual photo tiles/images within the opened gallery.
    "gallery_item": "div[aria-label*='Photo' i] img, a[href*='/photo/'] img",
}

_STYLE_URL_RE = re.compile(r"url\((['\"]?)(?P<url>.*?)\1\)")


def fetch_photos(
    poi: ResolvedPOI,
    get_context: Callable[[], BrowserContext],
    *,
    max_photos: int,
) -> list[Photo]:
    """Fetch up to `max_photos` deduplicated photos from `poi`'s Google Maps
    photo gallery, in the order they were retrieved (FR-3.2).

    Raises `NoPhotosAvailableError` if the page loads but no photos are
    found, `ScrapeBlockedError` if a CAPTCHA/consent interstitial blocks
    reaching the POI panel, and `OperationTimeoutError` if any Playwright
    navigation/wait/action call times out (FR-3.3).
    """
    context = get_context()
    page = context.new_page()
    try:
        _load_poi_page(page, poi.maps_url)
        _open_photo_gallery(page)
        photos = _collect_photos(page, max_photos)
        if not photos:
            raise NoPhotosAvailableError(
                f"No photos found in the Google Maps gallery for POI: {poi.maps_url}"
            )
        return photos
    finally:
        page.close()


def _load_poi_page(page: Page, maps_url: str) -> None:
    """Navigate to `maps_url` and wait for the main POI panel (FR-3.1)."""
    try:
        page.goto(maps_url)
    except PlaywrightTimeoutError as exc:
        raise OperationTimeoutError(f"Timed out navigating to {maps_url!r}: {exc}") from exc

    _raise_if_blocked(page)

    try:
        page.wait_for_selector(SELECTORS["poi_panel"], state="visible")
    except PlaywrightTimeoutError as exc:
        # The POI panel may never render *because* an interstitial is
        # blocking it -- check explicitly for that before concluding this is
        # a plain timeout (FR-3.3 requires an explicit selector check, never
        # inferring SCRAPE_BLOCKED from a bare timeout).
        _raise_if_blocked(page)
        raise OperationTimeoutError(
            f"Timed out waiting for the POI panel to render: {exc}"
        ) from exc


def _raise_if_blocked(page: Page) -> None:
    """Raise `ScrapeBlockedError` if a CAPTCHA or consent/cookie interstitial
    is present, detected explicitly via a dedicated `SELECTORS` entry rather
    than inferred from a timeout (FR-3.3)."""
    if page.locator(SELECTORS["captcha_interstitial"]).count() > 0:
        raise ScrapeBlockedError("Google Maps presented a CAPTCHA interstitial.")
    if page.locator(SELECTORS["consent_interstitial"]).count() > 0:
        raise ScrapeBlockedError("Google Maps presented a consent/cookie interstitial.")


def _open_photo_gallery(page: Page) -> None:
    """Click the photo-gallery entry point if present. If absent, this is
    treated as zero photos by the caller (`_collect_photos` will simply find
    no gallery items) rather than raised here directly, so a missing entry
    point and an empty-but-present gallery both surface as
    `NoPhotosAvailableError` uniformly."""
    entry = page.locator(SELECTORS["photo_gallery_entry"])
    if entry.count() == 0:
        return

    try:
        entry.first.click()
        page.wait_for_selector(SELECTORS["gallery_container"], state="visible")
    except PlaywrightTimeoutError as exc:
        _raise_if_blocked(page)
        raise OperationTimeoutError(f"Timed out opening the photo gallery: {exc}") from exc


def _collect_photos(page: Page, max_photos: int) -> list[Photo]:
    """Collect up to `max_photos` deduplicated photos from the (possibly
    empty) opened gallery, scrolling to load more until either the cap is
    reached or two consecutive scrolls add zero new unique photos (FR-3.2)."""
    photos: list[Photo] = []
    seen_keys: set[str] = set()

    processed = _consume_new_items(page, 0, photos, seen_keys, max_photos)

    consecutive_empty_scrolls = 0
    while len(photos) < max_photos and consecutive_empty_scrolls < 2:
        before = len(photos)
        _scroll_gallery(page)
        processed = _consume_new_items(page, processed, photos, seen_keys, max_photos)
        if len(photos) == before:
            consecutive_empty_scrolls += 1
        else:
            consecutive_empty_scrolls = 0

    return photos


def _consume_new_items(
    page: Page,
    processed: int,
    photos: list[Photo],
    seen_keys: set[str],
    max_photos: int,
) -> int:
    """Process gallery items from index `processed` onward (items newly
    revealed since the last check), appending newly-unique photos to `photos`
    in place. Returns the updated `processed` index."""
    items = page.locator(SELECTORS["gallery_item"])
    total = items.count()

    index = processed
    while index < total and len(photos) < max_photos:
        built = _build_photo(items.nth(index))
        index += 1
        if built is None:
            continue
        photo, key = built
        if key in seen_keys:
            continue
        seen_keys.add(key)
        photos.append(photo)

    return index


def _scroll_gallery(page: Page) -> None:
    """Best-effort scroll to trigger Google Maps' lazy-loading of additional
    gallery items. A plain mouse-wheel scroll stands in for a
    container-specific scroll here; whether the gallery needs a distinct
    scroll-container selector is another detail that needs live-page
    confirmation (AR-3.1)."""
    page.mouse.wheel(0, 4000)


def _build_photo(item: Locator) -> tuple[Photo, str] | None:
    """Build a `Photo` plus its dedup key for one gallery item: prefer
    extracting the underlying photo URL and downloading it, falling back to
    an element screenshot only when no URL can be extracted (FR-3.2). Returns
    `None` if the item couldn't be turned into a photo at all (e.g. the
    extracted URL failed to download)."""
    url = _extract_photo_url(item)
    if url is not None:
        try:
            photo_bytes, mime_type = _download_photo(url)
        except httpx.HTTPError:
            return None
        return (
            Photo(bytes=photo_bytes, mime_type=mime_type, source_url=url),
            _normalize_photo_url(url),
        )

    screenshot_bytes = item.screenshot()
    key = hashlib.sha256(screenshot_bytes).hexdigest()
    return Photo(bytes=screenshot_bytes, mime_type="image/png", source_url=None), key


def _extract_photo_url(item: Locator) -> str | None:
    """Best-effort extraction of the underlying full-resolution photo URL for
    a single gallery item, preferring (in order) a direct `src`, the first
    candidate in a `srcset`, or a CSS `background-image` URL -- returning
    `None` only when none of these are present (FR-3.2)."""
    src = item.get_attribute("src")
    if src:
        return src

    srcset = item.get_attribute("srcset")
    if srcset:
        first_candidate = srcset.split(",")[0].strip().split(" ")[0]
        if first_candidate:
            return first_candidate

    style = item.get_attribute("style")
    if style:
        match = _STYLE_URL_RE.search(style)
        if match:
            return match.group("url")

    return None


def _normalize_photo_url(url: str) -> str:
    """Strip size-carrying suffix params (everything from the first `=`
    onward, e.g. `.../photo.jpg=w408-h306-k-no` -> `.../photo.jpg`) so the
    same underlying photo requested at different sizes still dedups to one
    key (FR-3.2)."""
    return url.split("=", 1)[0]


def _download_photo(url: str) -> tuple[bytes, str]:
    """Download the image bytes for a directly-extracted photo URL."""
    response = httpx.get(url)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    if not content_type:
        guessed, _ = mimetypes.guess_type(url)
        content_type = guessed or ""
    return response.content, content_type or "image/jpeg"
