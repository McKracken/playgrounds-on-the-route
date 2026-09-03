"""Exception-based error propagation shared by every module.

Each module raises the typed exception for its stage instead of returning a
sentinel value; `cli.py` is the only place that catches these and builds the
FR-5.1 JSON output envelope. `INTERNAL_ERROR` has no dedicated subclass here —
it is `cli.py`'s catch-all for any *other*, unanticipated exception (AR-7.2).
"""

from __future__ import annotations

from playground_check.models import ErrorCode


class PlaygroundCheckError(Exception):
    """Base for every typed error in the pipeline. `code` matches a value of
    ErrorCode and is what ends up in the CLI JSON output's `error.code` field."""

    code: ErrorCode

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidInputError(PlaygroundCheckError):
    code = ErrorCode.INVALID_INPUT


class PoiNotFoundError(PlaygroundCheckError):
    code = ErrorCode.POI_NOT_FOUND


class NoPhotosAvailableError(PlaygroundCheckError):
    code = ErrorCode.NO_PHOTOS_AVAILABLE


class ScrapeBlockedError(PlaygroundCheckError):
    code = ErrorCode.SCRAPE_BLOCKED


class OperationTimeoutError(PlaygroundCheckError):
    code = ErrorCode.TIMEOUT


class ClassifierError(PlaygroundCheckError):
    code = ErrorCode.CLASSIFIER_ERROR


class ConfigError(PlaygroundCheckError):
    code = ErrorCode.CONFIG_ERROR
