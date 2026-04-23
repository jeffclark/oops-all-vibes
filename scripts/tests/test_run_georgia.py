"""Tests for scripts/run_georgia.py.

The orchestrator's dependencies (call_sonnet, write_outputs, record_stats) are
patched per-test so we can exercise each branch of the retry logic without
hitting the real API or touching disk.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from anthropic import APIError

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import scripts.run_georgia as run_module  # noqa: E402


FACTS = {
    "name": "Jeff Clark",
    "email": "jeff@clarkle.com",
    "linkedin_url": "https://www.linkedin.com/in/serialcreative",
    "linkedin_title": "Director of Product at LeagueApps",
    "projects": [
        {"title": "Autoscope", "description": "x", "link": "x", "image": "x"},
    ],
}
TODAY = "2026-04-22"


def _valid_html() -> str:
    body_text = "Jeff Clark is the person. " * 30
    return f"""<!DOCTYPE html><html><body>
<h1>Jeff Clark</h1><p>{body_text}</p>
<p>jeff@clarkle.com</p>
<a href="https://www.linkedin.com/in/serialcreative">LinkedIn</a>
<p>Autoscope</p>
{"<p>filler</p>" * 80}
</body></html>"""


def _valid_diary() -> str:
    return (
        f"---\ndate: {TODAY}\nimportance: 3\n---\n\n"
        "Today I built the orchestrator. It has two strikes and a fail-open.\n"
    )


def _patch_common(monkeypatch, tmp_path, *, record_sink=None):
    """Patch assemble_prompt/write_outputs/record_stats; return the record sink."""
    monkeypatch.setattr(run_module, "assemble_prompt", lambda run_date, repo_root=None: "PROMPT")
    if record_sink is None:
        record_sink = []
    monkeypatch.setattr(
        run_module,
        "record_stats",
        lambda *args, **kwargs: record_sink.append((args, kwargs)),
    )
    monkeypatch.setattr(run_module, "write_outputs", MagicMock())
    return record_sink


def _make_api_error() -> APIError:
    return APIError(message="boom", request=MagicMock(), body=None)


# ---------- happy path: first-try success ----------


def test_first_try_success_commits_and_returns_zero(monkeypatch, tmp_path):
    sink = _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(run_module, "call_sonnet", lambda prompt: (_valid_html(), _valid_diary()))

    rc = run_module.run(TODAY, FACTS, tmp_path)
    assert rc == 0
    run_module.write_outputs.assert_called_once()
    assert len(sink) == 1
    args, _ = sink[0]
    # args: date, attempts, validation_failures, api_errors, committed, start
    assert args[1] == 1
    assert args[4] is True  # committed


# ---------- validation fails twice ----------


def test_validation_fails_twice_no_commit_returns_one(monkeypatch, tmp_path):
    sink = _patch_common(monkeypatch, tmp_path)
    # HTML missing the email both times
    bad_html = _valid_html().replace("jeff@clarkle.com", "x@x.com")
    monkeypatch.setattr(run_module, "call_sonnet", lambda prompt: (bad_html, _valid_diary()))

    rc = run_module.run(TODAY, FACTS, tmp_path)
    assert rc == 1
    run_module.write_outputs.assert_not_called()
    args, _ = sink[0]
    assert args[1] == 2  # attempts
    assert args[4] is False  # not committed


# ---------- API error ----------


def test_api_error_no_retry_returns_one(monkeypatch, tmp_path):
    sink = _patch_common(monkeypatch, tmp_path)
    call_mock = MagicMock(side_effect=_make_api_error())
    monkeypatch.setattr(run_module, "call_sonnet", call_mock)

    rc = run_module.run(TODAY, FACTS, tmp_path)
    assert rc == 1
    call_mock.assert_called_once()  # no retry
    args, _ = sink[0]
    assert args[1] == 1  # attempts
    assert args[3] == 1  # api_errors
    assert args[4] is False  # not committed


# ---------- SonnetOutputError, then success ----------


def test_sonnet_output_error_then_success(monkeypatch, tmp_path):
    sink = _patch_common(monkeypatch, tmp_path)
    from scripts.call_sonnet import SonnetOutputError

    prompts_received = []

    def fake_call(prompt):
        prompts_received.append(prompt)
        if len(prompts_received) == 1:
            raise SonnetOutputError("missing <site>", raw="garbled")
        return _valid_html(), _valid_diary()

    monkeypatch.setattr(run_module, "call_sonnet", fake_call)

    rc = run_module.run(TODAY, FACTS, tmp_path)
    assert rc == 0
    run_module.write_outputs.assert_called_once()
    # Second prompt must include the tag hint
    assert "[validation-failure]" in prompts_received[1]
    assert "<site>...</site>" in prompts_received[1] and "<log>...</log>" in prompts_received[1]
    args, _ = sink[0]
    assert args[1] == 2  # attempts
    assert args[4] is True  # committed


# ---------- validation fail then success (diary issue) ----------


def test_diary_fail_then_success(monkeypatch, tmp_path):
    sink = _patch_common(monkeypatch, tmp_path)
    # First call: diary has wrong date. Second call: valid.
    bad_diary = "---\ndate: 2026-04-01\nimportance: 3\n---\n\n" + ("body " * 20)
    attempts = {"n": 0}
    prompts_received: list[str] = []

    def fake_call(prompt):
        prompts_received.append(prompt)
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _valid_html(), bad_diary
        return _valid_html(), _valid_diary()

    monkeypatch.setattr(run_module, "call_sonnet", fake_call)

    rc = run_module.run(TODAY, FACTS, tmp_path)
    assert rc == 0
    run_module.write_outputs.assert_called_once()
    # The retry prompt must contain the diary failure string
    assert "2026-04-01" in prompts_received[1]
    assert "[validation-failure]" in prompts_received[1]


# ---------- record_stats called on every exit path ----------


@pytest.mark.parametrize(
    "outcome_setup",
    [
        "success_first_try",
        "validation_twice",
        "api_error",
    ],
)
def test_record_stats_always_called(monkeypatch, tmp_path, outcome_setup):
    sink = _patch_common(monkeypatch, tmp_path)
    from scripts.call_sonnet import SonnetOutputError

    if outcome_setup == "success_first_try":
        monkeypatch.setattr(run_module, "call_sonnet", lambda p: (_valid_html(), _valid_diary()))
    elif outcome_setup == "validation_twice":
        bad_html = _valid_html().replace("Autoscope", "Autonotscope")
        monkeypatch.setattr(run_module, "call_sonnet", lambda p: (bad_html, _valid_diary()))
    elif outcome_setup == "api_error":
        monkeypatch.setattr(run_module, "call_sonnet", MagicMock(side_effect=_make_api_error()))

    run_module.run(TODAY, FACTS, tmp_path)
    assert len(sink) == 1  # record_stats fired exactly once


# ---------- add_retry_hint format ----------


def test_add_retry_hint_shape():
    out = run_module.add_retry_hint("PROMPT", ["fix the email", "fix the date"])
    assert out.startswith("PROMPT")
    assert "[validation-failure]" in out
    assert "- fix the email" in out
    assert "- fix the date" in out
    assert "[/validation-failure]" in out
