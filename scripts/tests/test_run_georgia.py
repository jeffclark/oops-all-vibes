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


# ---------- the archive-claims gate ----------


def _gate_repo(tmp_path):
    """A repo with two archived days; 2026-04-25 is a gap that never existed."""
    for sub in ("archive", "log", "feedback", "prompts"):
        (tmp_path / sub).mkdir(exist_ok=True)
    for d in ("2026-04-23", "2026-04-24"):
        (tmp_path / "archive" / f"{d}.html").write_text("<html></html>")
        (tmp_path / "log" / f"{d}.md").write_text(
            f"---\ndate: {d}\nimportance: 2\n---\n\nbody.\n"
        )
    return tmp_path


GATE_DIARY = "---\ndate: 2026-04-25\nimportance: 3\n---\n\nA diary entry, long enough.\n"


def _gate_html(archive_body: str) -> str:
    """Minimal page that passes validate_output, plus an archive section."""
    return (
        "<!DOCTYPE html><html><head><title>t</title></head><body>"
        "<p>Jeff Clark jeff@clarkle.com "
        "https://www.linkedin.com/in/serialcreative</p>"
        f'<section id="archive">{archive_body}</section>'
        + "<p>" + ("filler content for the body length check. " * 40) + "</p>"
        + "</body></html>"
    )


def _gate_facts():
    return {
        "name": "Jeff Clark",
        "email": "jeff@clarkle.com",
        "linkedin_url": "https://www.linkedin.com/in/serialcreative",
        "projects": [],
    }


def _run_gate(monkeypatch, tmp_path, pages):
    """Run the pipeline over a queue of Sonnet responses. Returns (code, calls)."""
    repo = _gate_repo(tmp_path)
    queue = list(pages)
    calls = []

    def fake_call(prompt):
        calls.append(prompt)
        return queue.pop(0), GATE_DIARY

    monkeypatch.setattr(run_module, "call_sonnet", fake_call)
    # The gate is what's under test, not prompt assembly.
    monkeypatch.setattr(run_module, "assemble_prompt", lambda *a, **k: "PROMPT")
    # no_commit=True, so git is never invoked.
    code = run_module.run("2026-04-25", _gate_facts(), repo, no_commit=True)
    return code, calls, repo


def test_gate_blocks_a_page_that_invents_an_archive_day(monkeypatch, tmp_path):
    """2026-04-25 has no snapshot — an entry for it is the site inventing
    its own history, so nothing ships and yesterday's site stays live."""
    invented = _gate_html('<li data-archive-date="2026-04-22">Day 0</li>')
    code, calls, repo = _run_gate(monkeypatch, tmp_path, [invented, invented])
    assert code == 1
    assert len(calls) == 2, "should have retried once"
    assert not (repo / "index.html").exists(), "no site written"


def test_gate_retry_hint_names_the_invented_day(monkeypatch, tmp_path):
    invented = _gate_html('<li data-archive-date="2026-04-22">Day 0</li>')
    _, calls, _ = _run_gate(monkeypatch, tmp_path, [invented, invented])
    assert "2026-04-22" in calls[1]
    assert "[validation-failure]" in calls[1]


def test_gate_lets_a_corrected_second_attempt_through(monkeypatch, tmp_path):
    bad = _gate_html('<li data-archive-date="2026-04-22">Day 0</li>')
    good = _gate_html('<li class="imp-2" data-archive-date="2026-04-23">Day 1</li>')
    code, calls, repo = _run_gate(monkeypatch, tmp_path, [bad, good])
    assert code == 0
    assert len(calls) == 2
    assert (repo / "index.html").exists()


def test_gate_ships_soft_claims_and_records_them(monkeypatch, tmp_path):
    """A wrong importance marker is real, but not worth a day of no site."""
    soft = _gate_html('<li class="imp-5" data-archive-date="2026-04-23">Day 1</li>')
    code, calls, repo = _run_gate(monkeypatch, tmp_path, [soft])
    assert code == 0
    assert len(calls) == 1, "soft findings must not trigger a retry"
    assert (repo / "index.html").exists()

    line = json.loads((repo / "stats.jsonl").read_text().strip().splitlines()[-1])
    assert line["committed"] is True
    assert any("importance" in w for w in line["archive_warnings"])


def test_gate_records_no_warnings_for_a_truthful_page(monkeypatch, tmp_path):
    good = _gate_html(
        '<li class="imp-2" data-archive-date="2026-04-23">Day 1</li>'
        '<li class="imp-2" data-archive-date="2026-04-24">Day 2</li>'
        '<li class="imp-3" data-archive-date="2026-04-25">Day 3</li>'
    )
    code, _, repo = _run_gate(monkeypatch, tmp_path, [good])
    assert code == 0
    line = json.loads((repo / "stats.jsonl").read_text().strip().splitlines()[-1])
    assert line["archive_warnings"] == []


def test_gate_verifies_the_finalized_page_not_the_raw_output(monkeypatch, tmp_path):
    """Links are canonicalized before the gate sees them, so a link Georgia
    wrote as /2026-04-23 must not be reported as broken."""
    html = _gate_html('<a href="/2026-04-23">Day 1</a>')
    code, calls, repo = _run_gate(monkeypatch, tmp_path, [html])
    assert code == 0
    assert 'href="/archive/2026-04-23.html"' in (repo / "index.html").read_text()


def test_gate_failure_in_the_checker_never_costs_a_day(monkeypatch, tmp_path):
    """A bug in verification must not become a new way for the site to go dark."""
    def boom(*a, **k):
        raise RuntimeError("checker exploded")

    monkeypatch.setattr(run_module, "verify_archive_claims", boom)
    good = _gate_html('<li data-archive-date="2026-04-23">Day 1</li>')
    code, calls, repo = _run_gate(monkeypatch, tmp_path, [good])
    assert code == 0
    assert (repo / "index.html").exists()
    line = json.loads((repo / "stats.jsonl").read_text().strip().splitlines()[-1])
    assert any("did not run" in w for w in line["archive_warnings"])


def test_written_page_has_exactly_one_injected_footer(monkeypatch, tmp_path):
    """finalize_html runs once — the gate must not cause a second injection."""
    good = _gate_html('<li data-archive-date="2026-04-23">Day 1</li>')
    _, _, repo = _run_gate(monkeypatch, tmp_path, [good])
    assert (repo / "index.html").read_text().count("today's prompt") == 1
