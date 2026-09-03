"""Unit tests for `playground_check.storage` (spec FR-6.1, FR-6.2, FR-6.3
Verify conditions). Uses pytest's built-in `tmp_path` fixture as
`output_dir` -- no real filesystem state outside the test's own sandbox.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from playground_check.decision_engine import (
    Decision,
    PlaygroundClassifier,
    decide_from_photos,
)
from playground_check.models import ClassificationResult, Photo, ResolvedPOI
from playground_check.storage import save_evidence


def _photo(tag: str) -> Photo:
    return Photo(
        bytes=f"photo-bytes-{tag}".encode(),
        mime_type="image/jpeg",
        source_url=f"https://example.com/{tag}.jpg",
    )


def _poi() -> ResolvedPOI:
    return ResolvedPOI(
        lat=1.23,
        lng=4.56,
        name="Central Park Playground!",
        maps_url="https://www.google.com/maps?q=1.23,4.56",
    )


class ScriptedClassifier(PlaygroundClassifier):
    """Minimal fake classifier -- same pattern as test_decision_engine.py --
    used here only to build a realistic negative `Decision` for the
    regression test below."""

    def __init__(self, results: list[ClassificationResult]) -> None:
        self._results = list(results)
        self.calls = 0

    def classify(self, photo: Photo) -> ClassificationResult:
        self.calls += 1
        return self._results[self.calls - 1]


def test_osm_decision_writes_nothing_and_creates_no_directory(tmp_path: Path) -> None:
    decision = Decision(
        label="playground nearby",
        method_used="osm",
        confidence=1.0,
        qualifying=[],
    )

    result = save_evidence(decision, _poi(), tmp_path)

    assert result == []
    assert list(tmp_path.iterdir()) == []


def test_gmaps_negative_decision_writes_nothing_and_creates_no_directory(
    tmp_path: Path,
) -> None:
    decision = Decision(
        label="no playground nearby",
        method_used="gmaps_photos",
        confidence=None,
        qualifying=[],
    )

    result = save_evidence(decision, _poi(), tmp_path)

    assert result == []
    assert list(tmp_path.iterdir()) == []


def test_gmaps_positive_decision_writes_photos_and_sidecars(tmp_path: Path) -> None:
    photo1, photo2 = _photo("a"), _photo("b")
    result1 = ClassificationResult(is_playground=True, confidence=0.9)
    result2 = ClassificationResult(is_playground=True, confidence=0.6)
    decision = Decision(
        label="playground nearby",
        method_used="gmaps_photos",
        confidence=0.6,
        qualifying=[(photo1, result1), (photo2, result2)],
    )
    poi = _poi()

    written = save_evidence(decision, poi, tmp_path)

    assert len(written) == 2

    # Exactly one per-run directory was created, named from the POI's slug.
    subdirs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(subdirs) == 1
    run_dir = subdirs[0]
    assert run_dir.name.startswith("central-park-playground-")

    for path_str, photo, result in zip(written, (photo1, photo2), (result1, result2)):
        photo_path = Path(path_str)
        assert photo_path.exists()
        assert photo_path.read_bytes() == photo.bytes
        assert photo_path.parent == run_dir

        sidecar_path = photo_path.with_suffix(".json")
        assert sidecar_path.exists()
        metadata = json.loads(sidecar_path.read_text())
        assert metadata["lat"] == poi.lat
        assert metadata["lng"] == poi.lng
        assert metadata["source_url"] == photo.source_url
        assert metadata["confidence"] == result.confidence
        assert "timestamp" in metadata and metadata["timestamp"]


def test_threshold_not_met_decision_from_decide_from_photos_writes_nothing(
    tmp_path: Path,
) -> None:
    """Regression case: threshold=2 requested, but only 1 of 3 photos is
    positive, so `decide_from_photos` already returns a *negative* Decision
    per the threshold-not-met rule -- confirm `save_evidence` given that
    negative Decision writes zero files, even though one photo was positive
    mid-run (spec FR-6.1's historically-important Verify case)."""
    photos = [_photo("a"), _photo("b"), _photo("c")]
    classifier = ScriptedClassifier(
        [
            ClassificationResult(is_playground=True, confidence=0.9),
            ClassificationResult(is_playground=False, confidence=0.2),
            ClassificationResult(is_playground=False, confidence=0.1),
        ]
    )

    decision = decide_from_photos(photos, classifier, threshold=2)
    assert decision.label == "no playground nearby"  # sanity: threshold not met

    result = save_evidence(decision, _poi(), tmp_path)

    assert result == []
    assert list(tmp_path.iterdir()) == []


def test_one_write_failure_is_skipped_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo1, photo2 = _photo("a"), _photo("b")
    result1 = ClassificationResult(is_playground=True, confidence=0.9)
    result2 = ClassificationResult(is_playground=True, confidence=0.6)
    decision = Decision(
        label="playground nearby",
        method_used="gmaps_photos",
        confidence=0.6,
        qualifying=[(photo1, result1), (photo2, result2)],
    )

    original_write_bytes = Path.write_bytes
    call_count = {"n": 0}

    def flaky_write_bytes(self: Path, data: bytes) -> int:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated disk failure")
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

    written = save_evidence(decision, _poi(), tmp_path)

    assert len(written) == 1
    surviving_path = Path(written[0])
    assert surviving_path.exists()
    assert surviving_path.read_bytes() == photo2.bytes
