"""Tests for scripts/record_stats.py and scripts/build_stats_page.py."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.build_stats_page import WINDOW, build_stats_page  # noqa: E402
from scripts.record_stats import record_stats  # noqa: E402


# ---------- record_stats ----------


def test_record_stats_appends_one_line(tmp_path):
    start = time.monotonic() - 7.3
    record_stats("2026-04-22", 1, [], 0, True, start, repo_root=tmp_path)
    lines = (tmp_path / "stats.jsonl").read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["date"] == "2026-04-22"
    assert parsed["attempts"] == 1
    assert parsed["committed"] is True
    assert parsed["validation_failures"] == []
    assert parsed["api_errors"] == 0
    assert parsed["duration_ms"] >= 7000


def test_record_stats_three_runs_three_lines(tmp_path):
    for i in range(3):
        record_stats(
            f"2026-04-{20 + i:02d}", 1, [], 0, True, time.monotonic(), repo_root=tmp_path
        )
    lines = (tmp_path / "stats.jsonl").read_text().splitlines()
    assert len(lines) == 3


def test_record_stats_flattens_validation_failures(tmp_path):
    vf = [["fix email"], ["fix date", "fix importance"]]
    record_stats("2026-04-22", 2, vf, 0, False, time.monotonic(), repo_root=tmp_path)
    parsed = json.loads((tmp_path / "stats.jsonl").read_text().splitlines()[0])
    assert parsed["validation_failures"] == ["fix email", "fix date", "fix importance"]


def test_record_stats_regenerates_stats_html(tmp_path):
    record_stats("2026-04-22", 1, [], 0, True, time.monotonic(), repo_root=tmp_path)
    assert (tmp_path / "stats.html").exists()


# ---------- build_stats_page ----------


def _write_stats(tmp_path: Path, entries: list[dict]) -> None:
    lines = [json.dumps(e) for e in entries]
    (tmp_path / "stats.jsonl").write_text("\n".join(lines) + "\n")


def test_build_stats_page_summary_math(tmp_path):
    entries = [
        {"date": "2026-04-20", "attempts": 1, "validation_failures": [], "api_errors": 0, "committed": True, "duration_ms": 4000},
        {"date": "2026-04-21", "attempts": 2, "validation_failures": ["x"], "api_errors": 0, "committed": True, "duration_ms": 10000},
        {"date": "2026-04-22", "attempts": 1, "validation_failures": [], "api_errors": 1, "committed": False, "duration_ms": 2000},
    ]
    _write_stats(tmp_path, entries)
    build_stats_page(repo_root=tmp_path)
    html = (tmp_path / "stats.html").read_text()

    assert ">3</span>" in html  # runs_total
    # first-try success = 1/3 (first entry). Others: 2 attempts, or not committed.
    assert "33.3%" in html
    # overall commit % = 2/3
    assert "66.7%" in html
    # avg duration = (4 + 10 + 2) / 3 = 5.33s
    assert "5.33s" in html


def test_build_stats_page_rows_reverse_chron(tmp_path):
    entries = [
        {"date": "2026-04-20", "attempts": 1, "validation_failures": [], "api_errors": 0, "committed": True, "duration_ms": 4000},
        {"date": "2026-04-21", "attempts": 1, "validation_failures": [], "api_errors": 0, "committed": True, "duration_ms": 4000},
        {"date": "2026-04-22", "attempts": 1, "validation_failures": [], "api_errors": 0, "committed": True, "duration_ms": 4000},
    ]
    _write_stats(tmp_path, entries)
    build_stats_page(repo_root=tmp_path)
    html = (tmp_path / "stats.html").read_text()
    i_22 = html.index("2026-04-22")
    i_21 = html.index("2026-04-21")
    i_20 = html.index("2026-04-20")
    assert i_22 < i_21 < i_20


def test_build_stats_page_last_30_only(tmp_path):
    entries = [
        {
            "date": f"2026-03-{d:02d}",
            "attempts": 1,
            "validation_failures": [],
            "api_errors": 0,
            "committed": True,
            "duration_ms": 1000,
        }
        for d in range(1, 36)  # 35 entries
    ]
    _write_stats(tmp_path, entries)
    build_stats_page(repo_root=tmp_path)
    html = (tmp_path / "stats.html").read_text()
    assert ">30</span>" in html  # rolled window
    # First 5 entries (2026-03-01 through 2026-03-05) excluded
    assert "2026-03-01" not in html
    assert "2026-03-05" not in html
    # Last entry included
    assert "2026-03-35" in html or "2026-03-35</td>" in html


def test_failed_runs_visually_distinguished(tmp_path):
    entries = [
        {"date": "2026-04-21", "attempts": 2, "validation_failures": ["bad"], "api_errors": 0, "committed": False, "duration_ms": 3000},
        {"date": "2026-04-22", "attempts": 1, "validation_failures": [], "api_errors": 0, "committed": True, "duration_ms": 3000},
    ]
    _write_stats(tmp_path, entries)
    build_stats_page(repo_root=tmp_path)
    html = (tmp_path / "stats.html").read_text()
    assert 'class="fail"' in html
    assert 'class="ok"' in html


def test_stats_html_has_no_javascript(tmp_path):
    entries = [
        {"date": "2026-04-22", "attempts": 1, "validation_failures": [], "api_errors": 0, "committed": True, "duration_ms": 1000},
    ]
    _write_stats(tmp_path, entries)
    build_stats_page(repo_root=tmp_path)
    html = (tmp_path / "stats.html").read_text().lower()
    assert "<script" not in html
    assert "javascript" not in html


def test_build_stats_page_truncates_long_failures(tmp_path):
    long = "a really really long failure reason " * 5
    entries = [
        {"date": "2026-04-22", "attempts": 2, "validation_failures": [long], "api_errors": 0, "committed": False, "duration_ms": 1000},
    ]
    _write_stats(tmp_path, entries)
    build_stats_page(repo_root=tmp_path)
    html = (tmp_path / "stats.html").read_text()
    assert "…" in html  # ellipsis present


def test_build_stats_page_handles_empty_jsonl(tmp_path):
    (tmp_path / "stats.jsonl").write_text("")
    build_stats_page(repo_root=tmp_path)
    assert (tmp_path / "stats.html").exists()


def test_build_stats_page_handles_missing_jsonl(tmp_path):
    build_stats_page(repo_root=tmp_path)
    assert (tmp_path / "stats.html").exists()


def test_stats_html_is_concise(tmp_path):
    entries = [
        {
            "date": f"2026-03-{d:02d}",
            "attempts": 1,
            "validation_failures": [],
            "api_errors": 0,
            "committed": True,
            "duration_ms": 1000,
        }
        for d in range(1, 31)
    ]
    _write_stats(tmp_path, entries)
    build_stats_page(repo_root=tmp_path)
    lines = (tmp_path / "stats.html").read_text().splitlines()
    assert len(lines) <= 150


# ---------- archive warnings column ----------


def _write_stats(tmp_path, entries):
    (tmp_path / "stats.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))


BASE_ENTRY = {
    "date": "2026-04-22", "attempts": 1, "validation_failures": [],
    "api_errors": 0, "committed": True, "duration_ms": 1000,
}


def test_warnings_column_shows_count_and_preview(tmp_path):
    _write_stats(tmp_path, [{**BASE_ENTRY, "archive_warnings": ["Day 3 is wrong", "count off"]}])
    build_stats_page(repo_root=tmp_path)
    html = (tmp_path / "stats.html").read_text()
    assert "<th>archive warnings</th>" in html
    assert "2 · Day 3 is wrong | count off" in html


def test_warnings_cell_empty_when_page_told_the_truth(tmp_path):
    _write_stats(tmp_path, [{**BASE_ENTRY, "archive_warnings": []}])
    build_stats_page(repo_root=tmp_path)
    assert '<td class="warn"></td>' in (tmp_path / "stats.html").read_text()


def test_long_warning_list_is_truncated_to_one_line(tmp_path):
    _write_stats(tmp_path, [{**BASE_ENTRY, "archive_warnings": [f"warning number {i}" for i in range(74)]}])
    build_stats_page(repo_root=tmp_path)
    html = (tmp_path / "stats.html").read_text()
    assert "74 · " in html
    assert "…" in html
    assert "warning number 73" not in html


def test_warning_text_is_escaped(tmp_path):
    _write_stats(tmp_path, [{**BASE_ENTRY, "archive_warnings": ['<script>alert("x")</script>']}])
    build_stats_page(repo_root=tmp_path)
    html = (tmp_path / "stats.html").read_text()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_summary_counts_runs_that_shipped_something_untrue(tmp_path):
    _write_stats(tmp_path, [
        {**BASE_ENTRY, "date": "2026-04-20", "archive_warnings": ["a"]},
        {**BASE_ENTRY, "date": "2026-04-21", "archive_warnings": []},
        {**BASE_ENTRY, "date": "2026-04-22", "archive_warnings": ["b", "c"]},
    ])
    build_stats_page(repo_root=tmp_path)
    html = (tmp_path / "stats.html").read_text()
    assert "runs with false archive claims" in html
    assert '<span class="v">2</span>' in html


def test_older_lines_without_the_key_still_render(tmp_path):
    """Every line written before this feature lacks archive_warnings."""
    _write_stats(tmp_path, [BASE_ENTRY])
    build_stats_page(repo_root=tmp_path)
    html = (tmp_path / "stats.html").read_text()
    assert '<td class="warn"></td>' in html
    assert "2026-04-22" in html
