"""Playground photo classification via a litellm-routed vision model (spec
Feature 4: FR-4.1, FR-4.2, FR-4.4, AR-4.1, AR-4.2).

`PlaygroundClassifier` (the pluggable interface, FR-4.1) lives in
`decision_engine` -- the module that consumes it -- and is imported here so
this module's `LiteLLMVisionClassifier` implements that single, shared
interface identity. `decision_engine` and `cli.py` depend on the interface,
never on the concrete `LiteLLMVisionClassifier`, so a future local/specialized
model can be substituted without touching either caller. This module itself
never special-cases the concrete vision classifier with an `isinstance`
check, for the same reason.

Routing through litellm rather than a single provider's SDK directly means
`--vision-model` can name any litellm-supported vision-capable model (e.g.
`anthropic/claude-haiku-4-5`, `gpt-4o`, `gemini/gemini-2.0-flash`) without a
code change (spec FR-4.2).
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import litellm
from PIL import Image

from playground_check.decision_engine import PlaygroundClassifier
from playground_check.errors import ClassifierError
from playground_check.models import ClassificationResult, Photo

# Conservative image limits for a litellm image_url content block (spec
# Integration Points / FR-4.2). Chosen to comfortably satisfy the current
# direct-API limits of the major vision providers litellm routes to, rather
# than maintaining a per-provider limit table. NOTE: an earlier draft of this
# spec incorrectly stated a 20MB base64-encoded payload limit; 10MB is
# correct (originally re-verified against Anthropic's own vision docs, back
# when this classifier called Anthropic directly -- see spec.md Change Log).
_MAX_BASE64_BYTES = 10 * 1024 * 1024
_MAX_DIMENSION = 8000

#: Upper bound on resize/re-encode iterations in `_resize_if_needed`, purely
#: as a safety net against a pathological image that never converges under
#: the byte cap -- ordinary photos converge in one or two iterations.
_MAX_RESIZE_ATTEMPTS = 20

_TOOL_NAME = "classify_playground"

#: Structured-output tool definition (spec AR-4.2), in litellm's OpenAI-style
#: function-calling shape. `strict: true` asks for guaranteed schema
#: conformance where the resolved provider path supports it; FR-4.4's
#: malformed-response handling in `LiteLLMVisionClassifier.classify` remains
#: as defense in depth for any response that still doesn't conform, or for
#: providers where `strict` isn't honored.
_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": (
            "Report whether the provided photo shows kid-playground equipment "
            "(e.g. slides, swings, jungle gyms, climbing structures) and how "
            "confident you are in that judgment."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "is_playground": {
                    "type": "boolean",
                    "description": "True if the photo shows kid-playground equipment.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Confidence in the judgment, from 0 to 1.",
                },
            },
            "required": ["is_playground", "confidence"],
            "additionalProperties": False,
        },
        "strict": True,
    },
}

_TOOL_CHOICE: dict[str, Any] = {"type": "function", "function": {"name": _TOOL_NAME}}

_PROMPT = (
    "Does this photo show kid-playground equipment -- slides, swings, jungle "
    "gyms, climbing structures, or similar? Use the classify_playground tool "
    "to report your answer."
)


def _base64_encoded_size(num_bytes: int) -> int:
    """Size in bytes of the base64 encoding of `num_bytes` raw bytes (base64
    expands every 3 raw bytes to 4 encoded bytes, rounded up to a multiple of
    4)."""
    return (num_bytes + 2) // 3 * 4


def _resize_if_needed(image_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Resize/re-encode `image_bytes` if needed so the base64-encoded payload
    stays under 10MB and pixel dimensions stay within 8000x8000 (spec FR-4.2).

    Returns the original bytes and `mime_type` unchanged when no resize is
    needed. Otherwise returns JPEG-encoded bytes and `"image/jpeg"` -- the
    `image_url` data URI sent to the API must match the actual bytes, so
    callers must use the returned mime type, not the photo's original one,
    once resizing has occurred.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            width, height = opened.size
            needs_resize = (
                width > _MAX_DIMENSION
                or height > _MAX_DIMENSION
                or _base64_encoded_size(len(image_bytes)) > _MAX_BASE64_BYTES
            )
            if not needs_resize:
                return image_bytes, mime_type
            image = opened.convert("RGB")
    except Exception:
        # Not decodable by Pillow (or some other read failure) -- don't raise
        # here. Send the bytes as-is and let the API call itself surface the
        # failure, which `classify` turns into a ClassifierError (FR-4.4).
        return image_bytes, mime_type

    if width > _MAX_DIMENSION or height > _MAX_DIMENSION:
        scale = min(_MAX_DIMENSION / width, _MAX_DIMENSION / height)
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.LANCZOS,
        )

    quality = 90
    data = b""
    for _ in range(_MAX_RESIZE_ATTEMPTS):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
        data = buffer.getvalue()
        if _base64_encoded_size(len(data)) <= _MAX_BASE64_BYTES:
            return data, "image/jpeg"
        if quality > 20:
            quality -= 15
        else:
            image = image.resize(
                (max(1, int(image.width * 0.75)), max(1, int(image.height * 0.75))),
                Image.LANCZOS,
            )

    # Best effort: return whatever the last attempt produced even if it's
    # still over the limit. An oversized payload is then rejected by the API
    # itself, which surfaces to the caller as a ClassifierError (FR-4.4).
    return data, "image/jpeg"


class LiteLLMVisionClassifier(PlaygroundClassifier):
    """v1 `PlaygroundClassifier` implementation, routed through litellm (spec
    FR-4.2, AR-4.2).

    `model` is required and has no default -- the CLI's `--vision-model` flag
    (FR-7.1) has no hardcoded default either (only an optional environment
    fallback) -- so this class must work with any litellm-recognized model
    string the caller supplies, e.g. `"anthropic/claude-haiku-4-5"`,
    `"gpt-4o"`, or `"gemini/gemini-2.0-flash"`.
    """

    def __init__(self, model: str) -> None:
        self._model = model

    def classify(self, photo: Photo) -> ClassificationResult:
        try:
            image_bytes, mime_type = _resize_if_needed(photo.bytes, photo.mime_type)
            encoded_data = base64.standard_b64encode(image_bytes).decode("ascii")
            data_uri = f"data:{mime_type};base64,{encoded_data}"

            response = litellm.completion(
                model=self._model,
                max_tokens=256,
                tools=[_TOOL_DEFINITION],
                tool_choice=_TOOL_CHOICE,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_uri}},
                            {"type": "text", "text": _PROMPT},
                        ],
                    }
                ],
            )
        except Exception as exc:
            raise ClassifierError(f"litellm classification call failed: {exc}") from exc

        tool_calls = getattr(response.choices[0].message, "tool_calls", None) or []
        tool_call = next(
            (call for call in tool_calls if getattr(call.function, "name", None) == _TOOL_NAME),
            None,
        )
        if tool_call is None:
            raise ClassifierError(
                "litellm response contained no classify_playground tool call"
            )

        try:
            tool_input = json.loads(tool_call.function.arguments)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ClassifierError(
                f"litellm tool call arguments were not valid JSON: {exc}"
            ) from exc

        if not isinstance(tool_input, dict):
            raise ClassifierError("litellm tool call arguments were not a JSON object")

        is_playground = tool_input.get("is_playground")
        confidence = tool_input.get("confidence")

        if not isinstance(is_playground, bool):
            raise ClassifierError(
                "litellm tool call arguments missing/malformed 'is_playground'"
            )
        # bool is a subclass of int in Python, so explicitly exclude it here --
        # a `confidence` of `true`/`false` must not be accepted as 1.0/0.0.
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise ClassifierError(
                "litellm tool call arguments missing/malformed 'confidence'"
            )
        if not (0.0 <= float(confidence) <= 1.0):
            raise ClassifierError(
                "litellm tool call arguments 'confidence' out of range [0, 1]"
            )

        return ClassificationResult(
            is_playground=is_playground, confidence=float(confidence)
        )
