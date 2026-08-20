"""Tests for the token-headroom reporting in scripts/build_stats_page.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.build_stats_page import _summarize, build_stats_page  # noqa: E402
from scripts.call_model import MAX_TOKENS  # noqa: E402


def _entry(date: str, output_tokens: int = 0, **over) -> dict:
    line = {
        "date": date, "attempts": 1, "validation_failures": [], "api_errors": 0,
        "committed": True, "duration_ms": 200_000, "archive_warnings": [],
        "input_tokens": 60_000, "output_tokens": output_tokens,
    }
    line.update(over)
    return line


def _build(tmp_path: Path, entries: list[dict]) -> str:
    (tmp_path / "stats.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n"
    )
    build_stats_page(repo_root=tmp_path)
    return (tmp_path / "stats.html").read_text()


def test_peak_is_the_max_not_the_average():
    """max_tokens is a ceiling, so the worst day is the one that matters."""
    summary = _summarize([
        _entry("2026-08-17", 19_000),
        _entry("2026-08-18", 22_000),
        _entry("2026-08-19", 30_673),
    ])
    assert summary["peak_output_tokens"] == 30_673
    assert summary["peak_output_pct"] == round(30_673 / MAX_TOKENS * 100, 1)


def test_peak_pct_is_measured_against_the_real_ceiling():
    """If MAX_TOKENS is raised or lowered the headroom figure must follow it."""
    summary = _summarize([_entry("2026-08-19", MAX_TOKENS // 2)])
    assert summary["peak_output_pct"] == 50.0


def test_summary_survives_lines_written_before_the_key_existed():
    old = {"date": "2026-05-01", "attempts": 1, "committed": True, "duration_ms": 1000}
    summary = _summarize([old, _entry("2026-08-19", 25_000)])
    assert summary["peak_output_tokens"] == 25_000


def test_empty_window_reports_zero_not_a_crash():
    summary = _summarize([])
    assert summary["peak_output_tokens"] == 0
    assert summary["peak_output_pct"] == 0.0


def test_page_shows_the_peak_and_the_ceiling(tmp_path):
    html = _build(tmp_path, [_entry("2026-08-19", 30_673)])
    assert "30,673" in html
    assert f"{MAX_TOKENS:,}" in html
    assert "output tokens" in html


def test_zero_renders_blank_rather_than_a_misleading_zero(tmp_path):
    """A failed run spent tokens it couldn't record; 0 must not read as free.

    Asserts on the token cell specifically — api_errors legitimately shows 0.
    """
    html = _build(tmp_path, [_entry("2026-08-20", 0, committed=False)])
    row = next(l for l in html.splitlines() if "<tr class=" in l)
    assert row.endswith("<td></td></tr>")


def test_a_real_count_renders_with_a_thousands_separator(tmp_path):
    html = _build(tmp_path, [_entry("2026-08-19", 30_673)])
    row = next(l for l in html.splitlines() if "<tr class=" in l)
    assert row.endswith("<td>30,673</td></tr>")


def test_headroom_cards_are_hidden_until_there_is_data(tmp_path):
    """Old lines report 0; a "0.0% of ceiling" card would read as measured."""
    old = {"date": "2026-05-01", "attempts": 1, "committed": True, "duration_ms": 1000}
    html = _build(tmp_path, [old])
    assert "peak output tokens" not in html
    # The note explaining the column stays; only the summary card is gated.
    assert f"of the {MAX_TOKENS:,} ceiling" not in html
    assert "output tokens" in html  # the column header is still there


def test_headroom_cards_appear_once_a_run_records_tokens(tmp_path):
    html = _build(tmp_path, [_entry("2026-08-19", 30_673)])
    assert "peak output tokens" in html
    assert f"of the {MAX_TOKENS:,} ceiling" in html
