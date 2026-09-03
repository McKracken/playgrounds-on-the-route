"""Unit tests for `playground_check.decision_engine` (spec FR-2.2, FR-4.3,
FR-4.4 Verify conditions). Uses a hand-written fake `PlaygroundClassifier`
subclass (not `unittest.mock`) so the "stops early" assertions can be
verified via a plain call counter.
"""

from __future__ import annotations

import pytest

from playground_check.decision_engine import (
    Decision,
    PlaygroundClassifier,
    decide_from_osm_hit,
    decide_from_photos,
)
from playground_check.errors import ClassifierError
from playground_check.models import ClassificationResult, Photo


def _photo(tag: str) -> Photo:
    return Photo(
        bytes=tag.encode(),
        mime_type="image/jpeg",
        source_url=f"https://example.com/{tag}.jpg",
    )


class ScriptedClassifier(PlaygroundClassifier):
    """A fake classifier driven by a fixed script of outcomes, one per call,
    consumed in order. An outcome is either a `ClassificationResult` to
    return or a `ClassifierError` instance to raise. Tracks `calls` so tests
    can assert early-stop / skip-and-continue behavior precisely."""

    def __init__(self, script: list[ClassificationResult | ClassifierError]) -> None:
        self._script = list(script)
        self.calls = 0

    def classify(self, photo: Photo) -> ClassificationResult:
        self.calls += 1
        outcome = self._script[self.calls - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_decide_from_osm_hit_returns_fixed_positive_decision() -> None:
    decision = decide_from_osm_hit()

    assert decision == Decision(
        label="playground nearby",
        method_used="osm",
        confidence=1.0,
        qualifying=[],
    )


def test_threshold_one_stops_after_first_positive_classification() -> None:
    photos = [_photo("a"), _photo("b"), _photo("c")]
    classifier = ScriptedClassifier(
        [
            ClassificationResult(is_playground=True, confidence=0.8),
            ClassificationResult(is_playground=True, confidence=0.99),
            ClassificationResult(is_playground=True, confidence=0.5),
        ]
    )

    decision = decide_from_photos(photos, classifier, threshold=1)

    assert classifier.calls == 1  # stopped immediately, photos b/c untouched
    assert decision.label == "playground nearby"
    assert decision.method_used == "gmaps_photos"
    assert decision.confidence == 0.8
    assert decision.qualifying == [
        (photos[0], ClassificationResult(is_playground=True, confidence=0.8))
    ]


def test_threshold_two_confidence_is_minimum_of_qualifying_photos() -> None:
    photos = [_photo("a"), _photo("b"), _photo("c")]
    classifier = ScriptedClassifier(
        [
            ClassificationResult(is_playground=True, confidence=0.9),
            ClassificationResult(is_playground=True, confidence=0.6),
            ClassificationResult(is_playground=True, confidence=0.99),
        ]
    )

    decision = decide_from_photos(photos, classifier, threshold=2)

    assert classifier.calls == 2  # third photo never classified
    assert decision.label == "playground nearby"
    assert decision.method_used == "gmaps_photos"
    assert decision.confidence == 0.6  # min(0.9, 0.6), not the third photo's 0.99
    assert [photo for photo, _ in decision.qualifying] == photos[:2]


def test_all_photos_exhausted_without_reaching_threshold_is_negative() -> None:
    photos = [_photo("a"), _photo("b"), _photo("c")]
    classifier = ScriptedClassifier(
        [
            ClassificationResult(is_playground=False, confidence=0.1),
            ClassificationResult(is_playground=True, confidence=0.4),
            ClassificationResult(is_playground=False, confidence=0.2),
        ]
    )

    decision = decide_from_photos(photos, classifier, threshold=2)

    assert classifier.calls == 3  # every photo was classified
    assert decision.label == "no playground nearby"
    assert decision.method_used == "gmaps_photos"
    assert decision.confidence is None
    assert decision.qualifying == []


def test_one_classifier_error_among_three_is_skipped_and_continues() -> None:
    photos = [_photo("a"), _photo("b"), _photo("c")]
    classifier = ScriptedClassifier(
        [
            ClassifierError("transient failure"),
            ClassificationResult(is_playground=False, confidence=0.3),
            ClassificationResult(is_playground=True, confidence=0.7),
        ]
    )

    decision = decide_from_photos(photos, classifier, threshold=1)

    assert classifier.calls == 3  # continued past the failed first photo
    assert decision.label == "playground nearby"
    assert decision.method_used == "gmaps_photos"
    assert decision.confidence == 0.7
    assert decision.qualifying == [
        (photos[2], ClassificationResult(is_playground=True, confidence=0.7))
    ]


def test_every_photo_raising_classifier_error_reraises_classifier_error() -> None:
    photos = [_photo("a"), _photo("b"), _photo("c")]
    classifier = ScriptedClassifier(
        [
            ClassifierError("a failed"),
            ClassifierError("b failed"),
            ClassifierError("c failed"),
        ]
    )

    with pytest.raises(ClassifierError):
        decide_from_photos(photos, classifier, threshold=1)

    assert classifier.calls == 3  # every photo was attempted before giving up
