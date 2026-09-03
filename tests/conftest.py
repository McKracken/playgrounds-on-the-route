"""Shared fixtures for the playground_check test suite."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_browser_context() -> MagicMock:
    """A stand-in for a Playwright BrowserContext, with no real browser behind it."""
    return MagicMock(name="FakeBrowserContext")


@pytest.fixture
def get_context_factory(fake_browser_context: MagicMock):
    """A `get_context: Callable[[], BrowserContext]` per AR-1.1's contract:
    returns the same fake context on every call, and records how many times
    it was invoked so tests can assert lazy/memoized creation behavior."""

    calls = {"count": 0}

    def _get_context() -> MagicMock:
        calls["count"] += 1
        return fake_browser_context

    _get_context.calls = calls  # type: ignore[attr-defined]
    return _get_context
