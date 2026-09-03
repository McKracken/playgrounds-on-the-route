"""Unit tests for `playground_check.input_resolver` (spec FR-1.1, FR-1.2,
FR-1.3, AR-1.1 Verify conditions). All Playwright/`httpx` calls are mocked --
no real browser or network access, per FR-8.1.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from playground_check.errors import InvalidInputError, OperationTimeoutError, PoiNotFoundError
from playground_check.input_resolver import resolve

# --------------------------------------------------------------------------
# FR-1.3: INVALID_INPUT -- detectable without ever attempting resolution.
# --------------------------------------------------------------------------


def test_empty_input_raises_invalid_input_without_browser_call(get_context_factory) -> None:
    with pytest.raises(InvalidInputError):
        resolve("", get_context_factory)
    assert get_context_factory.calls["count"] == 0


def test_whitespace_only_input_raises_invalid_input_without_browser_call(
    get_context_factory,
) -> None:
    with pytest.raises(InvalidInputError):
        resolve("   \n\t  ", get_context_factory)
    assert get_context_factory.calls["count"] == 0


def test_out_of_range_coordinate_raises_invalid_input_without_browser_call(
    get_context_factory,
) -> None:
    with pytest.raises(InvalidInputError):
        resolve("200,9", get_context_factory)
    assert get_context_factory.calls["count"] == 0


def test_coordinate_pair_with_overflowing_magnitude_raises_invalid_input(
    get_context_factory,
) -> None:
    """A syntactically valid but absurdly large magnitude overflows `float()`
    to `inf`, which must be caught by the non-finite check, not just the
    range check."""
    huge_digits = "1" + "0" * 400
    with pytest.raises(InvalidInputError):
        resolve(f"{huge_digits}.0,9.0", get_context_factory)
    assert get_context_factory.calls["count"] == 0


# --------------------------------------------------------------------------
# FR-1.2: bare coordinate pairs -- validated and used directly, no browser.
# --------------------------------------------------------------------------


def test_bare_valid_coordinate_synthesizes_maps_url(get_context_factory) -> None:
    result = resolve("48.8584, 2.2945", get_context_factory)

    assert result.lat == pytest.approx(48.8584)
    assert result.lng == pytest.approx(2.2945)
    assert result.name is None
    assert result.maps_url
    assert "48.8584" in result.maps_url
    assert "2.2945" in result.maps_url
    assert get_context_factory.calls["count"] == 0


# --------------------------------------------------------------------------
# FR-1.2: full Maps URL precise-coordinate extraction precedence.
# --------------------------------------------------------------------------


def test_full_url_prefers_data_coord_over_viewport(get_context_factory) -> None:
    url = (
        "https://www.google.com/maps/place/Some+Place/@40.0,-70.0,15z/"
        "data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d48.8584!4d2.2945"
    )

    result = resolve(url, get_context_factory)

    assert result.lat == pytest.approx(48.8584)
    assert result.lng == pytest.approx(2.2945)
    assert get_context_factory.calls["count"] == 0


def test_full_url_with_only_viewport_uses_viewport(get_context_factory) -> None:
    url = "https://www.google.com/maps/@48.8584,2.2945,15z"

    result = resolve(url, get_context_factory)

    assert result.lat == pytest.approx(48.8584)
    assert result.lng == pytest.approx(2.2945)
    assert get_context_factory.calls["count"] == 0


# --------------------------------------------------------------------------
# FR-1.2: short link redirect resolution (httpx mocked, no real network).
# --------------------------------------------------------------------------


def test_short_link_resolving_to_viewport_only_url_uses_viewport_no_browser(
    monkeypatch: pytest.MonkeyPatch, get_context_factory
) -> None:
    redirect_response = MagicMock(name="FakeRedirectResponse")
    redirect_response.url = "https://www.google.com/maps/@48.8584,2.2945,17z"
    monkeypatch.setattr(httpx, "get", MagicMock(return_value=redirect_response))

    result = resolve("https://maps.app.goo.gl/abc123", get_context_factory)

    assert result.lat == pytest.approx(48.8584)
    assert result.lng == pytest.approx(2.2945)
    assert get_context_factory.calls["count"] == 0


def test_short_link_resolving_to_place_id_only_url_triggers_browser_path(
    monkeypatch: pytest.MonkeyPatch, get_context_factory, fake_browser_context
) -> None:
    redirect_response = MagicMock(name="FakeRedirectResponse")
    redirect_response.url = "https://www.google.com/maps/place/?q=place_id:ChIJ123"
    monkeypatch.setattr(httpx, "get", MagicMock(return_value=redirect_response))

    fake_page = MagicMock(name="FakePage")
    fake_page.url = (
        "https://www.google.com/maps/place/Some+Place/@48.8584,2.2945,17z/"
        "data=!3d48.8584!4d2.2945"
    )
    fake_page.title.return_value = "Some Place - Google Maps"
    fake_browser_context.new_page.return_value = fake_page

    result = resolve("https://maps.app.goo.gl/abc123", get_context_factory)

    assert result.lat == pytest.approx(48.8584)
    assert result.lng == pytest.approx(2.2945)
    assert result.name == "Some Place"
    assert get_context_factory.calls["count"] == 1
    fake_page.goto.assert_called_once_with(redirect_response.url)


def test_short_link_http_get_uses_five_second_timeout_and_follows_redirects(
    monkeypatch: pytest.MonkeyPatch, get_context_factory
) -> None:
    redirect_response = MagicMock(name="FakeRedirectResponse")
    redirect_response.url = "https://www.google.com/maps/@48.8584,2.2945,17z"
    mock_get = MagicMock(return_value=redirect_response)
    monkeypatch.setattr(httpx, "get", mock_get)

    resolve("https://maps.app.goo.gl/abc123", get_context_factory)

    assert mock_get.call_count == 1
    _args, kwargs = mock_get.call_args
    assert kwargs["timeout"] == 5.0
    assert kwargs["follow_redirects"] is True


def test_short_link_http_timeout_raises_operation_timeout(
    monkeypatch: pytest.MonkeyPatch, get_context_factory
) -> None:
    monkeypatch.setattr(
        httpx, "get", MagicMock(side_effect=httpx.TimeoutException("timed out"))
    )

    with pytest.raises(OperationTimeoutError):
        resolve("https://maps.app.goo.gl/abc123", get_context_factory)

    assert get_context_factory.calls["count"] == 0


# --------------------------------------------------------------------------
# FR-1.2 / FR-1.3: free text and no-coordinate URLs go through the browser.
# --------------------------------------------------------------------------


def test_free_text_triggers_browser_path_and_resolves(
    get_context_factory, fake_browser_context
) -> None:
    fake_page = MagicMock(name="FakePage")
    fake_page.url = (
        "https://www.google.com/maps/place/Eiffel+Tower/@48.8584,2.2945,17z/"
        "data=!3d48.8584!4d2.2945"
    )
    fake_page.title.return_value = "Eiffel Tower - Google Maps"
    fake_browser_context.new_page.return_value = fake_page

    result = resolve("Eiffel Tower", get_context_factory)

    assert result.lat == pytest.approx(48.8584)
    assert result.lng == pytest.approx(2.2945)
    assert result.name == "Eiffel Tower"
    assert get_context_factory.calls["count"] == 1
    called_url = fake_page.goto.call_args[0][0]
    assert called_url.startswith("https://www.google.com/maps/search/")
    assert "Eiffel" in called_url


def test_browser_navigation_with_no_extractable_coord_raises_poi_not_found(
    get_context_factory, fake_browser_context
) -> None:
    fake_page = MagicMock(name="FakePage")
    fake_page.url = "https://www.google.com/maps/place/?q=some+ambiguous+query"
    fake_browser_context.new_page.return_value = fake_page

    with pytest.raises(PoiNotFoundError):
        resolve("https://www.google.com/maps/some/opaque/path", get_context_factory)


def test_free_text_gibberish_zero_result_raises_poi_not_found_not_invalid_input(
    get_context_factory, fake_browser_context
) -> None:
    """Gibberish free text is still classified as free text (FR-1.1), so a
    zero-result search for it must surface as POI_NOT_FOUND, never
    INVALID_INPUT (FR-1.3)."""
    fake_page = MagicMock(name="FakePage")
    fake_page.url = "https://www.google.com/maps/search/asdkjhaslkdjh+qwlekjqwlekj"
    fake_browser_context.new_page.return_value = fake_page

    with pytest.raises(PoiNotFoundError):
        resolve("asdkjhaslkdjh qwlekjqwlekj", get_context_factory)


def test_browser_navigation_timeout_raises_operation_timeout(
    get_context_factory, fake_browser_context
) -> None:
    fake_page = MagicMock(name="FakePage")
    fake_page.goto.side_effect = PlaywrightTimeoutError("Timeout 20000ms exceeded")
    fake_browser_context.new_page.return_value = fake_page

    with pytest.raises(OperationTimeoutError):
        resolve("Some Place With No Response", get_context_factory)
