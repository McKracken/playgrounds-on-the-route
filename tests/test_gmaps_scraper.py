"""Unit tests for `playground_check.gmaps_scraper` (spec FR-3.1, FR-3.2,
FR-3.3 Verify conditions). The Playwright `BrowserContext`/`Page`/`Locator`
objects are all mocked via `fake_browser_context`/`get_context_factory`
(tests/conftest.py) -- no real browser or network access, per FR-8.1. The
one exception is the `@pytest.mark.integration` test at the bottom, which is
excluded from the default run by `pyproject.toml`'s `-m 'not integration'`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from playground_check.errors import (
    NoPhotosAvailableError,
    OperationTimeoutError,
    ScrapeBlockedError,
)
from playground_check.gmaps_scraper import SELECTORS, fetch_photos
from playground_check.models import ResolvedPOI

POI = ResolvedPOI(
    lat=48.8584,
    lng=2.2945,
    name="Eiffel Tower",
    maps_url="https://www.google.com/maps/place/?q=place_id:abc123",
)


# ---------------------------------------------------------------------------
# Fake-DOM helpers
# ---------------------------------------------------------------------------


def _default_locator() -> MagicMock:
    """A locator standing in for "selector not found" (count() == 0) --
    used as the fallback for any selector a test doesn't explicitly wire up."""
    locator = MagicMock(name="DefaultLocator")
    locator.count.return_value = 0
    return locator


def _make_page(locator_map: dict[str, MagicMock]) -> MagicMock:
    """A fake Playwright Page whose `.locator(selector)` dispatches to
    `locator_map` by exact selector string (matched against `SELECTORS`
    values), falling back to a "not found" locator for anything else."""
    page = MagicMock(name="FakePage")
    page.locator.side_effect = lambda selector: locator_map.get(selector, _default_locator())
    return page


def _make_gallery_item(
    *, src: str | None = None, srcset: str | None = None, style: str | None = None
) -> MagicMock:
    """A fake gallery-item Locator whose `get_attribute` responds like a real
    `img`/`div` element with the given (possibly absent) attributes."""
    item = MagicMock(name="GalleryItem")
    attrs = {"src": src, "srcset": srcset, "style": style}
    item.get_attribute.side_effect = lambda name: attrs.get(name)
    return item


def _make_gallery_items_locator(items: list[MagicMock]) -> MagicMock:
    """A fake Locator representing the full (static) set of gallery items
    already present in the DOM -- `.count()`/`.nth()` behave like a real
    Playwright Locator over a fixed list."""
    locator = MagicMock(name="GalleryItemsLocator")
    locator.count.return_value = len(items)
    locator.nth.side_effect = lambda i: items[i]
    return locator


def _make_open_gallery_entry() -> MagicMock:
    entry = MagicMock(name="GalleryEntry")
    entry.count.return_value = 1
    return entry


def _fake_httpx_get(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patches `httpx.get` to return distinct fake bytes per URL, so tests
    never make a real network call (FR-8.1)."""

    def _get(url: str, *args: object, **kwargs: object) -> MagicMock:
        response = MagicMock(name="FakeImageResponse")
        response.raise_for_status.return_value = None
        response.headers = {"content-type": "image/jpeg"}
        response.content = f"bytes-for-{url}".encode()
        return response

    mock_get = MagicMock(side_effect=_get)
    monkeypatch.setattr(httpx, "get", mock_get)
    return mock_get


# ---------------------------------------------------------------------------
# FR-3.2 Verify: dedup + cap at max_photos, retrieval order preserved
# ---------------------------------------------------------------------------


def test_fetch_photos_dedups_and_caps_at_max_photos(
    get_context_factory, fake_browser_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = [
        "https://lh5.googleusercontent.com/p/photo1=w400-h300-k-no",
        "https://lh5.googleusercontent.com/p/photo2=w400-h300-k-no",
        "https://lh5.googleusercontent.com/p/photo1=w800-h600-k-no",  # dup of photo1, other size
        "https://lh5.googleusercontent.com/p/photo3=w400-h300-k-no",
        "https://lh5.googleusercontent.com/p/photo4=w400-h300-k-no",
    ]
    items = [_make_gallery_item(src=url) for url in urls]

    page = _make_page(
        {
            SELECTORS["photo_gallery_entry"]: _make_open_gallery_entry(),
            SELECTORS["gallery_item"]: _make_gallery_items_locator(items),
        }
    )
    fake_browser_context.new_page.return_value = page
    _fake_httpx_get(monkeypatch)

    photos = fetch_photos(POI, get_context_factory, max_photos=3)

    # 5 items but one is a dup (index 2), and the cap is 3: expect the first
    # 3 *unique* URLs in gallery order -- photo1, photo2, photo3 (photo4 never
    # reached because collection stops once the cap is hit).
    assert [p.source_url for p in photos] == [urls[0], urls[1], urls[3]]
    assert len(photos) == 3
    for photo in photos:
        assert photo.mime_type == "image/jpeg"
        assert photo.bytes == f"bytes-for-{photo.source_url}".encode()


def test_fetch_photos_returns_all_uniques_when_fewer_than_max_photos(
    get_context_factory, fake_browser_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = [
        "https://lh5.googleusercontent.com/p/only1=w400",
        "https://lh5.googleusercontent.com/p/only1=w800",  # dup
        "https://lh5.googleusercontent.com/p/only2=w400",
    ]
    items = [_make_gallery_item(src=url) for url in urls]

    page = _make_page(
        {
            SELECTORS["photo_gallery_entry"]: _make_open_gallery_entry(),
            SELECTORS["gallery_item"]: _make_gallery_items_locator(items),
        }
    )
    fake_browser_context.new_page.return_value = page
    _fake_httpx_get(monkeypatch)

    photos = fetch_photos(POI, get_context_factory, max_photos=10)

    assert [p.source_url for p in photos] == [urls[0], urls[2]]


def test_fetch_photos_scrolls_to_reveal_more_items_across_multiple_passes(
    get_context_factory, fake_browser_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates lazy-loading: the gallery only reveals 2 more items per
    scroll, so collecting 6 unique photos requires multiple scroll passes."""
    urls = [f"https://lh5.googleusercontent.com/p/photo{i}=w400" for i in range(6)]
    items = [_make_gallery_item(src=url) for url in urls]

    counts_seq = iter([2, 2, 4, 4, 6, 6, 6, 6])
    gallery_items_locator = MagicMock(name="GalleryItemsLocator")
    gallery_items_locator.count.side_effect = lambda: next(counts_seq, 6)
    gallery_items_locator.nth.side_effect = lambda i: items[i]

    page = _make_page(
        {
            SELECTORS["photo_gallery_entry"]: _make_open_gallery_entry(),
            SELECTORS["gallery_item"]: gallery_items_locator,
        }
    )
    fake_browser_context.new_page.return_value = page
    _fake_httpx_get(monkeypatch)

    photos = fetch_photos(POI, get_context_factory, max_photos=6)

    assert [p.source_url for p in photos] == urls


def test_fetch_photos_falls_back_to_element_screenshot_when_no_url_extractable(
    get_context_factory, fake_browser_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _make_gallery_item()  # no src / srcset / style
    item.screenshot.return_value = b"fake-png-bytes"

    page = _make_page(
        {
            SELECTORS["photo_gallery_entry"]: _make_open_gallery_entry(),
            SELECTORS["gallery_item"]: _make_gallery_items_locator([item]),
        }
    )
    fake_browser_context.new_page.return_value = page
    mock_get = MagicMock()
    monkeypatch.setattr(httpx, "get", mock_get)

    photos = fetch_photos(POI, get_context_factory, max_photos=5)

    assert len(photos) == 1
    assert photos[0].bytes == b"fake-png-bytes"
    assert photos[0].mime_type == "image/png"
    assert photos[0].source_url is None
    mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# FR-3.3 Verify: zero photos -> NoPhotosAvailableError
# ---------------------------------------------------------------------------


def test_fetch_photos_raises_no_photos_available_when_gallery_entry_absent(
    get_context_factory, fake_browser_context
) -> None:
    no_entry = MagicMock(name="GalleryEntry")
    no_entry.count.return_value = 0

    page = _make_page({SELECTORS["photo_gallery_entry"]: no_entry})
    fake_browser_context.new_page.return_value = page

    with pytest.raises(NoPhotosAvailableError):
        fetch_photos(POI, get_context_factory, max_photos=5)


def test_fetch_photos_raises_no_photos_available_when_gallery_present_but_empty(
    get_context_factory, fake_browser_context
) -> None:
    page = _make_page(
        {
            SELECTORS["photo_gallery_entry"]: _make_open_gallery_entry(),
            SELECTORS["gallery_item"]: _make_gallery_items_locator([]),
        }
    )
    fake_browser_context.new_page.return_value = page

    with pytest.raises(NoPhotosAvailableError):
        fetch_photos(POI, get_context_factory, max_photos=5)


# ---------------------------------------------------------------------------
# FR-3.3 Verify: CAPTCHA / consent interstitial -> ScrapeBlockedError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "selector_key", ["captcha_interstitial", "consent_interstitial"]
)
def test_fetch_photos_raises_scrape_blocked_on_interstitial(
    get_context_factory, fake_browser_context, selector_key: str
) -> None:
    blocking_locator = MagicMock(name="BlockingLocator")
    blocking_locator.count.return_value = 1

    page = _make_page({SELECTORS[selector_key]: blocking_locator})
    fake_browser_context.new_page.return_value = page

    with pytest.raises(ScrapeBlockedError):
        fetch_photos(POI, get_context_factory, max_photos=5)


# ---------------------------------------------------------------------------
# FR-3.3 Verify: Playwright TimeoutError -> OperationTimeoutError
# ---------------------------------------------------------------------------


def test_fetch_photos_raises_operation_timeout_on_navigation_timeout(
    get_context_factory, fake_browser_context
) -> None:
    page = _make_page({})
    page.goto.side_effect = PlaywrightTimeoutError("Timeout 20000ms exceeded.")
    fake_browser_context.new_page.return_value = page

    with pytest.raises(OperationTimeoutError):
        fetch_photos(POI, get_context_factory, max_photos=5)


def test_fetch_photos_raises_operation_timeout_when_poi_panel_never_renders(
    get_context_factory, fake_browser_context
) -> None:
    page = _make_page({})  # no interstitial selectors present -> genuine timeout
    page.wait_for_selector.side_effect = PlaywrightTimeoutError(
        "Timeout waiting for selector."
    )
    fake_browser_context.new_page.return_value = page

    with pytest.raises(OperationTimeoutError):
        fetch_photos(POI, get_context_factory, max_photos=5)


def test_fetch_photos_raises_scrape_blocked_not_timeout_when_panel_wait_times_out_with_interstitial_present(
    get_context_factory, fake_browser_context
) -> None:
    """When the POI-panel wait times out *and* a blocking interstitial is
    present, the interstitial takes precedence -- SCRAPE_BLOCKED, not
    TIMEOUT (FR-3.3: never infer blocking from a bare timeout, but do detect
    it explicitly when the selector is actually there)."""
    captcha_locator = MagicMock(name="CaptchaLocator")
    captcha_locator.count.return_value = 1

    page = _make_page({SELECTORS["captcha_interstitial"]: captcha_locator})
    page.wait_for_selector.side_effect = PlaywrightTimeoutError(
        "Timeout waiting for selector."
    )
    fake_browser_context.new_page.return_value = page

    with pytest.raises(ScrapeBlockedError):
        fetch_photos(POI, get_context_factory, max_photos=5)


def test_fetch_photos_memoized_context_used_once_per_call(
    get_context_factory, fake_browser_context
) -> None:
    """`get_context` is expected to return a single memoized context (AR-1.1)
    -- this scraper must call it and consume the same context, never
    constructing its own."""
    no_entry = MagicMock(name="GalleryEntry")
    no_entry.count.return_value = 0
    page = _make_page({SELECTORS["photo_gallery_entry"]: no_entry})
    fake_browser_context.new_page.return_value = page

    with pytest.raises(NoPhotosAvailableError):
        fetch_photos(POI, get_context_factory, max_photos=5)

    assert get_context_factory.calls["count"] == 1
    page.close.assert_called_once()


# ---------------------------------------------------------------------------
# FR-3.1 Verify: integration test (best-effort; excluded from default run)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_real_google_maps_poi_panel_detected() -> None:
    """Best-effort, network-gated integration test (FR-3.1 Verify): navigates
    to a known public Google Maps POI URL with a real Playwright browser and
    asserts the POI panel is detected within the timeout.

    Excluded from the default `pytest` run via pyproject.toml's
    `-m 'not integration'`. Not exercised in this sandbox (no network/browser
    access) -- per AR-3.1, `SELECTORS["poi_panel"]` is a best-effort guess
    and may need live confirmation/updating.
    """
    from playwright.sync_api import sync_playwright

    url = "https://www.google.com/maps/place/Eiffel+Tower/@48.8583701,2.2944813,17z"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(url, timeout=20000)
            page.wait_for_selector(SELECTORS["poi_panel"], state="visible", timeout=20000)
            assert page.locator(SELECTORS["poi_panel"]).count() > 0
        finally:
            context.close()
            browser.close()
