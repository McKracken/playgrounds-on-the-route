"""Unit tests for `playground_check.osm_lookup` (spec FR-2.1, FR-2.2, FR-2.3,
AR-2.1 Verify conditions). All `httpx` calls are mocked -- no real network
access, per FR-8.1.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from playground_check.osm_lookup import check_nearby

LAT = 48.8584
LNG = 2.2945
RADIUS = 150
TIMEOUT = 10
ENDPOINT = "https://overpass-api.de/api/interpreter"


def _make_response(total: str) -> MagicMock:
    """A stand-in for an httpx.Response carrying an Overpass `out count;` body."""
    response = MagicMock(name="FakeResponse")
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "elements": [{"type": "count", "tags": {"total": total}}]
    }
    return response


def test_nonzero_count_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_post = MagicMock(return_value=_make_response("3"))
    monkeypatch.setattr(httpx, "post", mock_post)

    result = check_nearby(LAT, LNG, radius=RADIUS, timeout=TIMEOUT, endpoint=ENDPOINT)

    assert result is True


def test_zero_count_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_post = MagicMock(return_value=_make_response("0"))
    monkeypatch.setattr(httpx, "post", mock_post)

    result = check_nearby(LAT, LNG, radius=RADIUS, timeout=TIMEOUT, endpoint=ENDPOINT)

    assert result is False


def test_relation_only_match_treated_identically_to_node_or_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The query's `nwr` selector already merges node/way/relation results into
    a single count, so a relation-only match is just another nonzero count --
    the parsing logic must not special-case element types (FR-2.1 Verify)."""
    mock_post = MagicMock(return_value=_make_response("1"))
    monkeypatch.setattr(httpx, "post", mock_post)

    result = check_nearby(LAT, LNG, radius=RADIUS, timeout=TIMEOUT, endpoint=ENDPOINT)

    assert result is True


@pytest.mark.parametrize(
    "exception",
    [
        httpx.TimeoutException("timed out"),
        httpx.ConnectError("connection failed"),
    ],
)
def test_timeout_or_connection_error_returns_false_without_raising(
    monkeypatch: pytest.MonkeyPatch, exception: Exception
) -> None:
    mock_post = MagicMock(side_effect=exception)
    monkeypatch.setattr(httpx, "post", mock_post)

    result = check_nearby(LAT, LNG, radius=RADIUS, timeout=TIMEOUT, endpoint=ENDPOINT)

    assert result is False


def test_non_2xx_response_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock(name="FakeErrorResponse")
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=MagicMock(), response=MagicMock()
    )
    mock_post = MagicMock(return_value=response)
    monkeypatch.setattr(httpx, "post", mock_post)

    result = check_nearby(LAT, LNG, radius=RADIUS, timeout=TIMEOUT, endpoint=ENDPOINT)

    assert result is False


def test_malformed_response_shape_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock(name="FakeMalformedResponse")
    response.raise_for_status.return_value = None
    response.json.return_value = {"elements": []}  # no count element at all
    mock_post = MagicMock(return_value=response)
    monkeypatch.setattr(httpx, "post", mock_post)

    result = check_nearby(LAT, LNG, radius=RADIUS, timeout=TIMEOUT, endpoint=ENDPOINT)

    assert result is False


def test_request_uses_user_agent_header_and_timeout_plus_five(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_post = MagicMock(return_value=_make_response("1"))
    monkeypatch.setattr(httpx, "post", mock_post)

    check_nearby(LAT, LNG, radius=RADIUS, timeout=TIMEOUT, endpoint=ENDPOINT)

    assert mock_post.call_count == 1
    _args, kwargs = mock_post.call_args
    headers = kwargs["headers"]
    assert "User-Agent" in headers
    assert headers["User-Agent"]  # non-empty, descriptive
    assert kwargs["timeout"] == TIMEOUT + 5


def test_request_sends_query_as_data_form_param(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_post = MagicMock(return_value=_make_response("1"))
    monkeypatch.setattr(httpx, "post", mock_post)

    check_nearby(LAT, LNG, radius=RADIUS, timeout=TIMEOUT, endpoint=ENDPOINT)

    args, kwargs = mock_post.call_args
    assert args[0] == ENDPOINT
    assert "data" in kwargs
    query = kwargs["data"]["data"]
    assert "nwr" in query
    assert '["leisure"="playground"]' in query
    assert f"around:{RADIUS},{LAT},{LNG}" in query
    assert f"timeout:{TIMEOUT}" in query
    assert "out count;" in query
