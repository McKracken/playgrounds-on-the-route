"""Unit tests for `playground_check.photo_classifier` (spec FR-4.1, FR-4.2,
FR-4.4, AR-4.1, AR-4.2 Verify conditions). `litellm.completion` is mocked
entirely -- no real API calls, no provider credential needed, per FR-8.1."""

from __future__ import annotations

import base64
import inspect
import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import litellm
import pytest
from PIL import Image

from playground_check import photo_classifier
from playground_check.decision_engine import PlaygroundClassifier
from playground_check.errors import ClassifierError
from playground_check.models import ClassificationResult, Photo
from playground_check.photo_classifier import LiteLLMVisionClassifier

MODEL = "anthropic/claude-haiku-4-5"


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


def _tool_call_response(is_playground: object, confidence: object) -> SimpleNamespace:
    """A litellm/OpenAI-shaped completion response with one tool call."""
    function = SimpleNamespace(
        name="classify_playground",
        arguments=json.dumps({"is_playground": is_playground, "confidence": confidence}),
    )
    tool_call = SimpleNamespace(function=function)
    message = SimpleNamespace(tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _no_tool_call_response() -> SimpleNamespace:
    message = SimpleNamespace(tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


# ---------------------------------------------------------------------------
# FR-4.2: positive / negative structured responses parse into a
# ClassificationResult.
# ---------------------------------------------------------------------------


def test_positive_tool_use_response_parses_into_classification_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_completion = MagicMock(return_value=_tool_call_response(True, 0.9))
    monkeypatch.setattr(litellm, "completion", fake_completion)

    classifier = LiteLLMVisionClassifier(model=MODEL)
    result = classifier.classify(_make_small_photo())

    assert result == ClassificationResult(is_playground=True, confidence=0.9)


def test_negative_tool_use_response_parses_into_classification_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_completion = MagicMock(return_value=_tool_call_response(False, 0.1))
    monkeypatch.setattr(litellm, "completion", fake_completion)

    classifier = LiteLLMVisionClassifier(model=MODEL)
    result = classifier.classify(_make_small_photo())

    assert result == ClassificationResult(is_playground=False, confidence=0.1)


def test_request_uses_forced_tool_choice_and_matching_tool_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_completion = MagicMock(return_value=_tool_call_response(True, 0.7))
    monkeypatch.setattr(litellm, "completion", fake_completion)

    classifier = LiteLLMVisionClassifier(model=MODEL)
    classifier.classify(_make_small_photo())

    _, kwargs = fake_completion.call_args
    assert kwargs["model"] == MODEL
    assert kwargs["tool_choice"] == {
        "type": "function",
        "function": {"name": "classify_playground"},
    }

    assert len(kwargs["tools"]) == 1
    tool = kwargs["tools"][0]
    assert tool["type"] == "function"
    function = tool["function"]
    assert function["name"] == "classify_playground"
    assert function["strict"] is True
    schema = function["parameters"]
    assert schema["required"] == ["is_playground", "confidence"]
    assert set(schema["properties"]) == {"is_playground", "confidence"}
    assert schema["properties"]["is_playground"]["type"] == "boolean"
    assert schema["properties"]["confidence"]["type"] == "number"

    image_block = kwargs["messages"][0]["content"][0]
    assert image_block["type"] == "image_url"
    url = image_block["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    # No resize needed for this small photo -- the data URI's payload
    # reflects the original bytes untouched.
    assert base64.standard_b64decode(url.split(",", 1)[1])


# ---------------------------------------------------------------------------
# FR-4.2: oversized image is resized before the API call.
# ---------------------------------------------------------------------------


def test_oversized_image_is_resized_before_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_completion = MagicMock(return_value=_tool_call_response(True, 0.8))
    monkeypatch.setattr(litellm, "completion", fake_completion)

    classifier = LiteLLMVisionClassifier(model=MODEL)
    photo = _make_oversized_photo()
    assert Image.open(io.BytesIO(photo.bytes)).size == (9000, 100)

    result = classifier.classify(photo)

    assert result == ClassificationResult(is_playground=True, confidence=0.8)

    _, kwargs = fake_completion.call_args
    image_block = kwargs["messages"][0]["content"][0]
    url = image_block["image_url"]["url"]
    # Resizing re-encodes as JPEG, so the data URI's declared type must
    # reflect that -- it must never claim "image/png" while shipping JPEG
    # bytes.
    assert url.startswith("data:image/jpeg;base64,")

    encoded = url.split(",", 1)[1]
    sent_bytes = base64.standard_b64decode(encoded)
    with Image.open(io.BytesIO(sent_bytes)) as resized:
        assert resized.format == "JPEG"
        width, height = resized.size
    assert width <= 8000
    assert height <= 8000
    assert len(encoded.encode("ascii")) <= 10 * 1024 * 1024


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
    fake_completion = MagicMock(side_effect=litellm.exceptions.APIConnectionError(
        message="boom", llm_provider="anthropic", model=MODEL
    ))
    monkeypatch.setattr(litellm, "completion", fake_completion)

    classifier = LiteLLMVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


def test_response_with_no_tool_use_block_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_completion = MagicMock(return_value=_no_tool_call_response())
    monkeypatch.setattr(litellm, "completion", fake_completion)

    classifier = LiteLLMVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


def test_tool_use_missing_confidence_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function = SimpleNamespace(
        name="classify_playground", arguments=json.dumps({"is_playground": True})
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[SimpleNamespace(function=function)]))]
    )
    monkeypatch.setattr(litellm, "completion", MagicMock(return_value=response))

    classifier = LiteLLMVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


def test_tool_use_missing_is_playground_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function = SimpleNamespace(
        name="classify_playground", arguments=json.dumps({"confidence": 0.5})
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[SimpleNamespace(function=function)]))]
    )
    monkeypatch.setattr(litellm, "completion", MagicMock(return_value=response))

    classifier = LiteLLMVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


def test_tool_use_malformed_json_arguments_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    function = SimpleNamespace(name="classify_playground", arguments="{not valid json")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[SimpleNamespace(function=function)]))]
    )
    monkeypatch.setattr(litellm, "completion", MagicMock(return_value=response))

    classifier = LiteLLMVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


def test_tool_use_non_boolean_is_playground_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_completion = MagicMock(return_value=_tool_call_response("yes", 0.5))
    monkeypatch.setattr(litellm, "completion", fake_completion)

    classifier = LiteLLMVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


def test_tool_use_confidence_out_of_range_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_completion = MagicMock(return_value=_tool_call_response(True, 1.5))
    monkeypatch.setattr(litellm, "completion", fake_completion)

    classifier = LiteLLMVisionClassifier(model=MODEL)
    with pytest.raises(ClassifierError):
        classifier.classify(_make_small_photo())


# ---------------------------------------------------------------------------
# FR-4.1: pluggable interface -- a fake classifier drives through the ABC
# alone, and this module never does an isinstance check against the concrete
# LiteLLMVisionClassifier.
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


def test_module_never_checks_isinstance_against_litellm_vision_classifier() -> None:
    """Design constraint (spec FR-4.1): decision_engine/cli must be able to
    drive any PlaygroundClassifier purely through the ABC, so this module
    itself must not special-case the concrete LiteLLMVisionClassifier."""
    source = inspect.getsource(photo_classifier)
    offending_lines = [
        line
        for line in source.splitlines()
        if "isinstance" in line and "LiteLLMVisionClassifier" in line
    ]
    assert offending_lines == []
