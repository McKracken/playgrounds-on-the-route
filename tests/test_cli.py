"""Tests for the CLI orchestration layer (spec FR-4.5, FR-5.1, FR-5.2, FR-7.1,
FR-7.2, AR-7.1, AR-7.2, AR-1.1's cli.py-ownership half)."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from playground_check import cli
from playground_check.decision_engine import Decision
from playground_check.errors import NoPhotosAvailableError
from playground_check.models import ResolvedPOI

_ALL_FLAGS = [
    "--osm-radius",
    "--osm-timeout",
    "--osm-endpoint",
    "--max-photos",
    "--threshold",
    "--vision-model",
    "--page-timeout",
    "--output-dir",
    "--output-file",
]

_POI = ResolvedPOI(lat=45.0, lng=9.0, name="Some Park", maps_url="https://maps.example/some-park")


def test_help_exits_0_and_lists_every_flag() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "playground_check.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    for flag in _ALL_FLAGS:
        assert flag in result.stdout


def test_omitting_vision_model_exits_2_before_any_pipeline_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cli._VISION_MODEL_ENV_VAR, raising=False)
    fake_resolve = MagicMock()
    monkeypatch.setattr(cli.input_resolver, "resolve", fake_resolve)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["some input"])

    assert exc_info.value.code == 2
    fake_resolve.assert_not_called()


def test_vision_model_env_var_used_when_flag_omitted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(cli._VISION_MODEL_ENV_VAR, "anthropic/claude-from-env")
    monkeypatch.setattr(cli.input_resolver, "resolve", MagicMock(return_value=_POI))
    monkeypatch.setattr(cli.osm_lookup, "check_nearby", MagicMock(return_value=True))
    monkeypatch.setattr(cli.storage, "save_evidence", MagicMock(return_value=[]))

    exit_code = cli.main(["some input"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["error"] is None


def test_vision_model_flag_overrides_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cli._VISION_MODEL_ENV_VAR, "anthropic/claude-from-env")
    parser = cli._build_parser()
    args = parser.parse_args(["some input", "--vision-model", "anthropic/claude-from-flag"])
    assert args.vision_model == "anthropic/claude-from-flag"


def test_load_dotenv_called_before_argument_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """AR-7.3: .env is loaded before the parser is built, so a
    PLAYGROUND_CHECK_VISION_MODEL or provider credential set only in .env is
    already visible to argparse's defaults and later credential checks."""
    monkeypatch.setenv(cli._VISION_MODEL_ENV_VAR, "anthropic/claude-from-env")
    call_order: list[str] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: call_order.append("load_dotenv"))

    real_build_parser = cli._build_parser

    def _recording_build_parser():
        call_order.append("build_parser")
        return real_build_parser()

    monkeypatch.setattr(cli, "_build_parser", _recording_build_parser)
    monkeypatch.setattr(cli.input_resolver, "resolve", MagicMock(return_value=_POI))
    monkeypatch.setattr(cli.osm_lookup, "check_nearby", MagicMock(return_value=True))
    monkeypatch.setattr(cli.storage, "save_evidence", MagicMock(return_value=[]))

    cli.main(["some input"])

    assert call_order == ["load_dotenv", "build_parser"]


def test_osm_positive_end_to_end(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake_gmaps_fetch = MagicMock()
    fake_classifier_cls = MagicMock()
    fake_save_evidence = MagicMock(return_value=[])

    monkeypatch.setattr(cli.input_resolver, "resolve", MagicMock(return_value=_POI))
    monkeypatch.setattr(cli.osm_lookup, "check_nearby", MagicMock(return_value=True))
    monkeypatch.setattr(cli.gmaps_scraper, "fetch_photos", fake_gmaps_fetch)
    monkeypatch.setattr(cli, "LiteLLMVisionClassifier", fake_classifier_cls)
    monkeypatch.setattr(cli.storage, "save_evidence", fake_save_evidence)

    exit_code = cli.main(["some input", "--vision-model", "claude-fake"])

    assert exit_code == 0
    fake_gmaps_fetch.assert_not_called()
    fake_classifier_cls.assert_not_called()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "input": "some input",
        "resolved": {"lat": 45.0, "lng": 9.0, "name": "Some Park", "maps_url": "https://maps.example/some-park"},
        "label": "playground nearby",
        "method_used": "osm",
        "confidence": 1.0,
        "evidence": [],
        "error": None,
    }


def test_gmaps_fallback_end_to_end(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    decision = Decision(
        label="playground nearby",
        method_used="gmaps_photos",
        confidence=0.87,
        qualifying=[],
    )
    fake_decide_from_photos = MagicMock(return_value=decision)

    monkeypatch.setattr(
        cli.litellm, "validate_environment", MagicMock(return_value={"keys_in_environment": True, "missing_keys": []})
    )
    monkeypatch.setattr(cli.input_resolver, "resolve", MagicMock(return_value=_POI))
    monkeypatch.setattr(cli.osm_lookup, "check_nearby", MagicMock(return_value=False))
    monkeypatch.setattr(cli.gmaps_scraper, "fetch_photos", MagicMock(return_value=["photo-1", "photo-2"]))
    monkeypatch.setattr(cli, "LiteLLMVisionClassifier", MagicMock())
    monkeypatch.setattr(cli.decision_engine, "decide_from_photos", fake_decide_from_photos)
    monkeypatch.setattr(cli.storage, "save_evidence", MagicMock(return_value=["output/some-park-x/photo-000.jpg"]))

    exit_code = cli.main(["some input", "--vision-model", "claude-fake"])

    assert exit_code == 0
    fake_decide_from_photos.assert_called_once()

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "input": "some input",
        "resolved": {"lat": 45.0, "lng": 9.0, "name": "Some Park", "maps_url": "https://maps.example/some-park"},
        "label": "playground nearby",
        "method_used": "gmaps_photos",
        "confidence": 0.87,
        "evidence": ["output/some-park-x/photo-000.jpg"],
        "error": None,
    }


def test_missing_credential_returns_config_error_without_scraping(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_gmaps_fetch = MagicMock()

    monkeypatch.setattr(
        cli.litellm,
        "validate_environment",
        MagicMock(return_value={"keys_in_environment": False, "missing_keys": ["ANTHROPIC_API_KEY"]}),
    )
    monkeypatch.setattr(cli.input_resolver, "resolve", MagicMock(return_value=_POI))
    monkeypatch.setattr(cli.osm_lookup, "check_nearby", MagicMock(return_value=False))
    monkeypatch.setattr(cli.gmaps_scraper, "fetch_photos", fake_gmaps_fetch)

    exit_code = cli.main(["some input", "--vision-model", "claude-fake"])

    assert exit_code == 0
    fake_gmaps_fetch.assert_not_called()

    output = json.loads(capsys.readouterr().out)
    assert output["error"]["code"] == "CONFIG_ERROR"
    assert "ANTHROPIC_API_KEY" in output["error"]["message"]
    assert output["resolved"] is not None  # resolution succeeded before this check
    assert output["label"] is None


def test_invalid_input_returns_error_with_null_resolved(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from playground_check.errors import InvalidInputError

    monkeypatch.setattr(
        cli.input_resolver, "resolve", MagicMock(side_effect=InvalidInputError("empty"))
    )

    exit_code = cli.main(["", "--vision-model", "claude-fake"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == {"code": "INVALID_INPUT", "message": "empty"}
    assert output["resolved"] is None
    assert output["evidence"] == []


def test_no_photos_available_error_preserves_resolved(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli.litellm, "validate_environment", MagicMock(return_value={"keys_in_environment": True, "missing_keys": []})
    )
    monkeypatch.setattr(cli.input_resolver, "resolve", MagicMock(return_value=_POI))
    monkeypatch.setattr(cli.osm_lookup, "check_nearby", MagicMock(return_value=False))
    monkeypatch.setattr(
        cli.gmaps_scraper,
        "fetch_photos",
        MagicMock(side_effect=NoPhotosAvailableError("no photos")),
    )

    exit_code = cli.main(["some input", "--vision-model", "claude-fake"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == {"code": "NO_PHOTOS_AVAILABLE", "message": "no photos"}
    assert output["resolved"] is not None
    assert output["label"] is None


def test_unhandled_exception_becomes_internal_error_and_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli.input_resolver, "resolve", MagicMock(side_effect=RuntimeError("boom"))
    )

    exit_code = cli.main(["some input", "--vision-model", "claude-fake"])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"]["code"] == "INTERNAL_ERROR"
    assert output["input"] == "some input"
    assert output["evidence"] == []
    assert output["resolved"] is None


def test_config_invariants_reject_bad_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resolve = MagicMock()
    monkeypatch.setattr(cli.input_resolver, "resolve", fake_resolve)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["some input", "--vision-model", "claude-fake", "--threshold", "0"])

    assert exc_info.value.code == 2
    fake_resolve.assert_not_called()


def test_context_factory_creates_exactly_one_context_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AR-1.1: exactly one Playwright browser/context is created per
    invocation, no matter how many times get_context() is called, and it's
    torn down by close() even though it was created lazily."""
    fake_context = MagicMock(name="context")
    fake_browser = MagicMock(name="browser")
    fake_browser.new_context.return_value = fake_context
    fake_playwright = MagicMock(name="playwright")
    fake_playwright.chromium.launch.return_value = fake_browser
    fake_start = MagicMock(return_value=fake_playwright)
    monkeypatch.setattr(cli, "sync_playwright", MagicMock(return_value=MagicMock(start=fake_start)))

    get_context, close = cli._make_context_factory(page_timeout_seconds=20)

    # Never calling get_context() at all must never launch anything.
    close()
    fake_start.assert_not_called()

    get_context, close = cli._make_context_factory(page_timeout_seconds=20)
    first = get_context()
    second = get_context()
    third = get_context()

    assert first is second is third is fake_context
    fake_start.assert_called_once()
    fake_playwright.chromium.launch.assert_called_once()
    fake_browser.new_context.assert_called_once()
    fake_context.set_default_timeout.assert_called_once_with(20 * 1000)
    fake_context.set_default_navigation_timeout.assert_called_once_with(20 * 1000)

    close()
    fake_context.close.assert_called_once()
    fake_browser.close.assert_called_once()
    fake_playwright.stop.assert_called_once()


def test_output_file_matches_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
) -> None:
    monkeypatch.setattr(cli.input_resolver, "resolve", MagicMock(return_value=_POI))
    monkeypatch.setattr(cli.osm_lookup, "check_nearby", MagicMock(return_value=True))
    monkeypatch.setattr(cli.storage, "save_evidence", MagicMock(return_value=[]))

    output_file = tmp_path / "result.json"
    cli.main(
        [
            "some input",
            "--vision-model",
            "claude-fake",
            "--output-file",
            str(output_file),
        ]
    )

    stdout_json = json.loads(capsys.readouterr().out)
    file_json = json.loads(output_file.read_text())
    assert stdout_json == file_json
