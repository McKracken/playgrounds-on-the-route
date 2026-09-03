"""Classification decision logic (spec Feature 2 FR-2.2; Feature 4 FR-4.3,
FR-4.4).

Defines the `PlaygroundClassifier` interface consumed by this module (spec
FR-4.1) -- the (separately-built) `ClaudeVisionClassifier` implements this
exact shape so `decision_engine` never needs an `isinstance` check against a
concrete classifier -- and the `Decision` result type consumed by `storage.py`
and the future CLI orchestrator (spec Feature 5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass

from playground_check.errors import ClassifierError
from playground_check.models import ClassificationResult, Photo


class PlaygroundClassifier(ABC):
    """Pluggable photo classifier interface (spec FR-4.1). `decision_engine`
    interacts with classifiers only through this interface -- a future
    local/specialized model can be substituted without touching this module.
    """

    @abstractmethod
    def classify(self, photo: Photo) -> ClassificationResult: ...


@dataclass(frozen=True)
class Decision:
    """The outcome of the decision stage (spec Feature 5), consumed by
    `storage.save_evidence` and the future CLI orchestrator."""

    label: str  # "playground nearby" | "no playground nearby"
    method_used: str | None  # "osm" | "gmaps_photos"
    confidence: float | None
    #: Positive (`is_playground=True`) `(photo, result)` pairs that
    #: contributed to a positive label, in classification order. Empty for a
    #: negative label or an OSM-sourced decision.
    qualifying: list[tuple[Photo, ClassificationResult]]


def decide_from_osm_hit() -> Decision:
    """Finalize a positive decision from an OSM hit (spec FR-2.2).

    Called only once the (separately-built) OSM lookup has already confirmed
    a hit -- by definition of being called at all, the answer is positive, so
    this never needs to inspect any input.
    """
    return Decision(
        label="playground nearby",
        method_used="osm",
        confidence=1.0,
        qualifying=[],
    )


def decide_from_photos(
    photos: Iterable[Photo],
    classifier: PlaygroundClassifier,
    *,
    threshold: int,
) -> Decision:
    """Classify `photos` one at a time, in the order given, stopping as soon
    as `threshold` positive (`is_playground=True`) results have been found
    (spec FR-4.3).

    A `ClassifierError` raised for a single photo is skipped -- not counted
    as positive or negative -- and classification continues with the next
    photo (spec FR-4.4). If *every* photo's classification call raises
    `ClassifierError` (zero successful classifications at all), this
    re-raises `ClassifierError` itself rather than returning a confident
    negative `Decision`, since the caller needs to know classification
    entirely failed rather than that it confidently found nothing.
    """
    qualifying: list[tuple[Photo, ClassificationResult]] = []
    attempted = 0
    succeeded = 0
    last_error: ClassifierError | None = None

    for photo in photos:
        attempted += 1
        try:
            result = classifier.classify(photo)
        except ClassifierError as exc:
            last_error = exc
            continue

        succeeded += 1
        if result.is_playground:
            qualifying.append((photo, result))
            if len(qualifying) >= threshold:
                break

    if attempted > 0 and succeeded == 0:
        # Every attempted photo failed to classify -- surface the failure
        # rather than reporting a confident negative.
        assert last_error is not None  # guaranteed: succeeded == 0 implies at
        # least one ClassifierError was caught above, since attempted > 0.
        raise last_error

    if len(qualifying) >= threshold:
        confidence = min(result.confidence for _, result in qualifying)
        return Decision(
            label="playground nearby",
            method_used="gmaps_photos",
            confidence=confidence,
            qualifying=qualifying,
        )

    return Decision(
        label="no playground nearby",
        method_used="gmaps_photos",
        confidence=None,
        qualifying=[],
    )
