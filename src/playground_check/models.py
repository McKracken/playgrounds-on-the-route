"""Shared value types used across every playground_check module (spec Data Requirements)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCode(str, Enum):
    """Values of the CLI JSON output's `error.code` field (spec Data Requirements)."""

    INVALID_INPUT = "INVALID_INPUT"
    POI_NOT_FOUND = "POI_NOT_FOUND"
    NO_PHOTOS_AVAILABLE = "NO_PHOTOS_AVAILABLE"
    SCRAPE_BLOCKED = "SCRAPE_BLOCKED"
    TIMEOUT = "TIMEOUT"
    CLASSIFIER_ERROR = "CLASSIFIER_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class ResolvedPOI:
    """A POI resolved to canonical coordinates (spec FR-1.2)."""

    lat: float
    lng: float
    name: str | None
    maps_url: str


@dataclass(frozen=True)
class Photo:
    """A single photo retrieved from a POI's Maps gallery (spec FR-3.2)."""

    bytes: bytes
    mime_type: str
    source_url: str | None


@dataclass(frozen=True)
class ClassificationResult:
    """The outcome of classifying one Photo (spec FR-4.1). Only produced for a
    classification call that completed — a failed call (FR-4.4) skips the photo
    instead of producing a result with a null confidence, so `confidence` here
    is always a real float, never None."""

    is_playground: bool
    confidence: float
