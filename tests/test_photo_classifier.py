"""Unit tests for `playground_check.photo_classifier` (spec FR-4.1, FR-4.2,
FR-4.4, AR-4.1, AR-4.2 Verify conditions). The `anthropic` client is mocked
entirely -- no real API calls, no `ANTHROPIC_API_KEY` needed, per FR-8.1."""

from __future__ import annotations

import base64
import inspect
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import pytest
from PIL import Image

from playground_check import photo_classifier
from playground_check.errors import ClassifierError
from playground_check.models import ClassificationResult, Photo
from playground_check.photo_classifier import (
    ClaudeVisionClassifier,
    PlaygroundClassifier,
)

MODEL = "claude-opus-5"


def _make_small_photo(mime_type: str = "image/jpeg") -> Photo:
    """A tiny, well-under-the-limits JPEG photo -- exercises the "no resize
    needed" path."""
    image = Image.new("RGB", (10, 10), color=(10, 120, 220))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return Photo(
        bytes=buffer.getvalue(),
        mime_type=mime_type,
        source_url="https://example.com/photo.jpg",
    )


def _make_oversized_photo() -> Photo:
    """A solid-color PNG well over the 8000px dimension limit on one side."""
    image = Image.new("RGB", (9000, 100), color=(30, 180, 60))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Photo(
        bytes=buffer.getvalue(),
        mime_type="image/png",
        source_url="https://example.com/big.png",
    )


def _tool_use_response(is_playground: object, confidence: object) -> SimpleNamespace:
    tool_use_block = SimpleNamespace(
        type="tool_use",
        name="classify_playground",
        id="toolu_01",
        input={"is_playground": is_playground, "confidence": confidence},
    )
    return SimpleNamespace(content=[tool_use_block])


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake_client: MagicMock) -> None:
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake_client)


# ---------------------------------------------------------------------------
# FR-4.2: positive / negative structured responses parse into a
# ClassificationResult.
# ---------------------------------------------------------------------------


def test_positive_tool_use_response_parses_into_classification_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use_response(True, 0.9)
    _patch_client(monkeypatch, fake_client)

    classifier = ClaudeVisionClassifier(model=MODEL)
    result = classifier.classify(_make_small_photo())

    assert result == ClassificationResult(is_playground=True, confidence=0.9)


def test_negative_tool_use_response_parses_into_classification_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use_response(False, 0.1)
    _patch_client(monkeypatch, fake_client)

    classifier = ClaudeVisionClassifier(model=MODEL)
    result = classifier.classify(_make_small_photo())

    assert result == ClassificationResult(is_playground=False, confidence=0.1)


def test_request_uses_forced_tool_choice_and_matching_tool_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use_response(True, 0.7)
    _patch_client(monkeypatch, fake_client)

    classifier = ClaudeVisionClassifier(model=MODEL)
    classifier.classify(_make_small_photo())

    _, kwargs = fake_client.messages.create.call_args
    assert kwargs["model"] == MODEL
    assert kwargs["tool_choice"] == {"type": "tool", "name": "classify_playground"}

    assert len(kwargs["tools"]) == 1
    tool = kwargs["tools"][0]
    assert tool["name"] == "classify_playground"
    assert tool["strict"] is True
    schema = tool["input_schema"]
    assert schema["required"] == ["is_playground", "confidence"]
    assert set(schema["properties"]) == {"is_playground", "confidence"}
    assert schema["properties"]["is_playground"]["type"] == "boolean"
    assert schema["properties"]["confidence"]["type"] == "number"

    image_block = kwargs["messages"][0]["content"][0]
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
    # No resize needed for this small photo -- media_type/data reflect the
    # original bytes untouched.
    assert image_block["source"]["media_type"] == "image/jpeg"
    assert base64.standard_b64decode(image_block["source"]["data"])


# ---------------------------------------------------------------------------
# FR-4.2: oversized image is resized before the API call.
# ---------------------------------------------------------------------------


def test_oversized_image_is_resized_before_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use_response(True, 0.8)
    _patch_client(monkeypatch, fake_client)

    classifier = ClaudeVisionClassifier(model=MODEL)
    photo = _make_oversized_photo()
    assert Image.open(io.BytesIO(photo.bytes)).size == (9000, 100)

    result = classifier.classify(photo)

    assert result == ClassificationResult(is_playground=True, confidence=0.8)

    _, kwargs = fake_client.messages.create.call_args
    image_block = kwargs["messages"][0]["content"][0]
    # Resizing re-encodes as JPEG, so media_type must reflect that -- it must
    # never claim "image/png" while shipping JPEG bytes.
    assert image_block["source"]["media_type"] == "image/jpeg"

    sent_bytes = base64.standard_b64decode(image_block["source"]["data"])
    with Image.open(io.BytesIO(sent_bytes)) as resized:
        assert resized.format == "JPEG"
        width, height = resized.size
    assert width <= 8000
    assert height <= 8000
    assert len(image_block["source"]["data"].encode("ascii")) <= 10 * 1024 * 1024


def test_resize_helper_is_a_noop_for_a_small_in_limits_image() -> None:
    photo = _make_small_photo()

    result_bytes, result_mime = photo_classifier._resize_if_needed(
        photo.bytes, photo.mime_type
    )

    assert result_bytes == photo.bytes
    assert result_mime == photo.mime_type


# ---------------------------------------------------------------------------
# FR-4.4: any failure for a classification call raises ClassifierError,
# never a placeholder ClassificationResult.
# ---------------------------------------------------------------------------


def test_api_exception_raises_classifier_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = anthropic.APIConnectionError(
        request=MagicMock()
    )
    _patch_client(monkeypatch, fake_client)

    classifier = ClaudeVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


def test_response_with_no_tool_use_block_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    text_block = SimpleNamespace(type="text", text="I can't help with that.")
    fake_client.messages.create.return_value = SimpleNamespace(content=[text_block])
    _patch_client(monkeypatch, fake_client)

    classifier = ClaudeVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


def test_tool_use_missing_confidence_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    tool_use_block = SimpleNamespace(
        type="tool_use", input={"is_playground": True}  # confidence missing
    )
    fake_client.messages.create.return_value = SimpleNamespace(content=[tool_use_block])
    _patch_client(monkeypatch, fake_client)

    classifier = ClaudeVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


def test_tool_use_missing_is_playground_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    tool_use_block = SimpleNamespace(
        type="tool_use", input={"confidence": 0.5}  # is_playground missing
    )
    fake_client.messages.create.return_value = SimpleNamespace(content=[tool_use_block])
    _patch_client(monkeypatch, fake_client)

    classifier = ClaudeVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


def test_tool_use_non_boolean_is_playground_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use_response("yes", 0.5)
    _patch_client(monkeypatch, fake_client)

    classifier = ClaudeVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


def test_tool_use_confidence_out_of_range_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _tool_use_response(True, 1.5)
    _patch_client(monkeypatch, fake_client)

    classifier = ClaudeVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


# ---------------------------------------------------------------------------
# FR-4.1: pluggable interface -- a fake classifier drives through the ABC
# alone, and this module never does an isinstance check against the concrete
# ClaudeVisionClassifier.
# ---------------------------------------------------------------------------


class _FakeClassifier(PlaygroundClassifier):
    """A stand-in classifier used to prove callers only need the ABC."""

    def __init__(self, result: ClassificationResult) -> None:
        self._result = result

    def classify(self, photo: Photo) -> ClassificationResult:
        return self._result


def test_fake_classifier_subclass_drives_through_abc_interface() -> None:
    expected = ClassificationResult(is_playground=True, confidence=0.42)
    fake: PlaygroundClassifier = _FakeClassifier(expected)

    assert fake.classify(_make_small_photo()) == expected


def test_playground_classifier_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        PlaygroundClassifier()  # type: ignore[abstract]


def test_module_never_checks_isinstance_against_claude_vision_classifier() -> None:
    """Design constraint (spec FR-4.1): decision_engine/cli must be able to
    drive any PlaygroundClassifier purely through the ABC, so this module
    itself must not special-case the concrete ClaudeVisionClassifier."""
    source = inspect.getsource(photo_classifier)
    offending_lines = [
        line
        for line in source.splitlines()
        if "isinstance" in line and "ClaudeVisionClassifier" in line
    ]
    assert offending_lines == []
