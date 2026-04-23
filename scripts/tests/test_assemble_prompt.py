"""Tests for scripts/assemble_prompt.py."""
from __future__ import annotations

import io
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# Ensure the repo root is on sys.path so `from scripts.assemble_prompt import ...` works
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.assemble_prompt import (  # noqa: E402
    DAY_1_FEEDBACK_SENTINEL,
    DAY_1_HISTORY_SENTINEL,
    FETCHER_FAILED_FEEDBACK_SENTINEL,
    IMPORTANCE_DECAY_DAYS,
    LogEntry,
    OLDER_TOP_N,
    RECENCY_WINDOW_DAYS,
    assemble_prompt,
    build_history_block,
    load_log_entries,
    pick_no_feedback_sentinel,
    render_feedback_narrative,
    score_older_entry,
    split_entries,
)


def _write_log(log_dir: Path, entry_date: date, importance: int | str | None, body: str = "note") -> Path:
    """Helper: write a YYYY-MM-DD.md log file with optional importance frontmatter."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{entry_date.isoformat()}.md"
    if importance is None:
        path.write_text(f"---\ndate: {entry_date.isoformat()}\n---\n\n{body}\n")
    else:
        path.write_text(
            f"---\ndate: {entry_date.isoformat()}\nimportance: {importance}\n---\n\n{body}\n"
        )
    return path


# ---------- scoring ----------


def test_score_older_entry_known_inputs():
    assert score_older_entry(5, 0) == pytest.approx(5.0)
    assert score_older_entry(1, IMPORTANCE_DECAY_DAYS) == pytest.approx(math.exp(-1))
    assert score_older_entry(3, 90) == pytest.approx(3 * math.exp(-90 / IMPORTANCE_DECAY_DAYS))


def test_score_respects_importance_over_recency():
    # AC: A (100d, importance 5) should rank above B (30d, importance 1)
    score_a = score_older_entry(5, 100)
    score_b = score_older_entry(1, 30)
    assert score_a > score_b


# ---------- feedback rendering ----------


def _full_feedback() -> dict:
    return {
        "date": "2026-04-21",
        "yesterday": {"visitors": 142, "pageviews": 289},
        "recent": {
            "last_7_days_visitors": 487,
            "last_7_days_avg": 69.6,
            "last_30_days_visitors": 1204,
            "last_30_days_avg": 40.1,
        },
        "historical": {
            "all_time_visitors": 8430,
            "days_live": 68,
            "peak_day": {"date": "2026-03-15", "visitors": 512},
        },
        "trend": {"yesterday_vs_7d_avg": 2.04, "week_over_week_pct": 32.0},
        "jeff_note": None,
    }


def test_render_feedback_full():
    out = render_feedback_narrative(_full_feedback())
    assert "Yesterday's feedback (2026-04-21):" in out
    assert "142 visitors" in out
    assert "289 pageviews" in out
    assert "487 people" in out
    assert "1,204 visitors" in out
    assert "8,430 total visitors" in out
    assert "68 days" in out
    assert "2026-03-15" in out
    assert "512 visitors" in out
    assert "2.04×" in out
    assert "up 32%" in out


def test_render_feedback_missing_wow_trend_still_renders():
    data = _full_feedback()
    del data["trend"]["week_over_week_pct"]
    out = render_feedback_narrative(data)
    assert "2.04×" in out
    assert "Week-over-week" not in out


def test_render_feedback_all_numeric_null_no_crash():
    data = {
        "date": "2026-04-21",
        "yesterday": {"visitors": None, "pageviews": None},
        "recent": {
            "last_7_days_visitors": None,
            "last_7_days_avg": None,
            "last_30_days_visitors": None,
            "last_30_days_avg": None,
        },
        "historical": {
            "all_time_visitors": None,
            "days_live": None,
            "peak_day": None,
        },
        "trend": {"yesterday_vs_7d_avg": None, "week_over_week_pct": None},
        "jeff_note": None,
    }
    out = render_feedback_narrative(data)
    assert "Yesterday's feedback (2026-04-21):" in out
    assert "null" not in out.lower()


def test_render_feedback_jeff_note_when_set():
    data = _full_feedback()
    data["jeff_note"] = "don't get cute today"
    out = render_feedback_narrative(data)
    assert "Jeff says: don't get cute today" in out


def test_render_feedback_wow_down_sign():
    data = _full_feedback()
    data["trend"]["week_over_week_pct"] = -12.0
    out = render_feedback_narrative(data)
    assert "down 12%" in out


# ---------- splitting ----------


def test_split_entries_all_recent():
    today = date(2026, 4, 22)
    entries = [LogEntry(today - timedelta(days=i), importance=3, body="x") for i in range(5)]
    recent, older = split_entries(entries, today)
    assert len(recent) == 5
    assert older == []
    assert [e.entry_date for e in recent] == sorted(e.entry_date for e in recent)  # oldest first


def test_split_entries_caps_older_at_top_n():
    today = date(2026, 4, 22)
    entries = (
        [LogEntry(today - timedelta(days=i), importance=3, body="r") for i in range(3)]
        + [LogEntry(today - timedelta(days=20 + i), importance=2, body="o") for i in range(50)]
    )
    recent, older = split_entries(entries, today)
    assert len(recent) == 3
    assert len(older) == OLDER_TOP_N


def test_split_entries_importance_over_recency():
    # A: 100 days old, importance 5 → score 5*exp(-100/180) ≈ 2.86
    # B: 30 days old, importance 1 → score 1*exp(-30/180) ≈ 0.85
    # Only one slot kept (via artificial cap) → A should win.
    today = date(2026, 4, 22)
    a = LogEntry(today - timedelta(days=100), importance=5, body="a")
    b = LogEntry(today - timedelta(days=30), importance=1, body="b")
    _, older = split_entries([a, b], today)
    assert older[0] is a  # A surfaced ahead of B


# ---------- loading log entries ----------


def test_load_log_entries_defaults_when_importance_missing(tmp_path, capsys):
    log_dir = tmp_path / "log"
    _write_log(log_dir, date(2026, 4, 1), importance=None)
    entries = load_log_entries(log_dir)
    assert len(entries) == 1
    assert entries[0].importance == 2  # DEFAULT_IMPORTANCE
    err = capsys.readouterr().err
    assert "invalid importance" in err


def test_load_log_entries_defaults_when_importance_out_of_range(tmp_path, capsys):
    log_dir = tmp_path / "log"
    _write_log(log_dir, date(2026, 4, 1), importance=9)
    entries = load_log_entries(log_dir)
    assert entries[0].importance == 2
    err = capsys.readouterr().err
    assert "invalid importance" in err


def test_load_log_entries_ignores_nondate_files(tmp_path):
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    (log_dir / "notes.md").write_text("free text")
    (log_dir / ".gitkeep").write_text("")
    _write_log(log_dir, date(2026, 4, 1), importance=3)
    entries = load_log_entries(log_dir)
    assert len(entries) == 1
    assert entries[0].entry_date == date(2026, 4, 1)


# ---------- history block ----------


def test_build_history_block_day_1_sentinel_when_empty():
    out = build_history_block([], date(2026, 4, 22))
    assert out == DAY_1_HISTORY_SENTINEL


def test_build_history_block_has_both_sections():
    today = date(2026, 4, 22)
    entries = [
        LogEntry(today - timedelta(days=1), importance=3, body="fresh"),
        LogEntry(today - timedelta(days=50), importance=4, body="vintage"),
    ]
    out = build_history_block(entries, today)
    assert "Recent history" in out
    assert "Older" in out
    assert "fresh" in out
    assert "vintage" in out


def test_build_history_block_omits_older_when_only_recent():
    today = date(2026, 4, 22)
    entries = [LogEntry(today - timedelta(days=1), importance=3, body="fresh")]
    out = build_history_block(entries, today)
    assert "Recent history" in out
    assert "Older" not in out


# ---------- no-feedback sentinel picker ----------


def test_pick_sentinel_day_1_when_archive_empty(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / ".gitkeep").write_text("")
    assert pick_no_feedback_sentinel(archive) == DAY_1_FEEDBACK_SENTINEL


def test_pick_sentinel_fetcher_failed_when_archive_has_entries(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "2026-04-20.html").write_text("<html></html>")
    assert pick_no_feedback_sentinel(archive) == FETCHER_FAILED_FEEDBACK_SENTINEL


def test_pick_sentinel_missing_archive_dir_treated_as_day_1(tmp_path):
    assert pick_no_feedback_sentinel(tmp_path / "no-such-dir") == DAY_1_FEEDBACK_SENTINEL


# ---------- end-to-end assemble ----------


def _make_fake_repo(tmp_path: Path) -> Path:
    """Create the minimum repo layout assemble_prompt needs."""
    (tmp_path / "georgia-soul.md").write_text("# Georgia\nSoul content.\n")
    (tmp_path / "facts.json").write_text(json.dumps({
        "name": "Jeff Clark",
        "email": "jeff@clarkle.com",
        "linkedin_url": "https://www.linkedin.com/in/serialcreative",
        "linkedin_title": "Director of Product at LeagueApps",
        "projects": [{"title": "Autoscope", "description": "x", "link": "x", "image": "x"}],
    }))
    (tmp_path / "log").mkdir()
    (tmp_path / "archive").mkdir()
    (tmp_path / "feedback").mkdir()
    return tmp_path


def test_assemble_prompt_day_1(tmp_path):
    repo = _make_fake_repo(tmp_path)
    out = assemble_prompt(date(2026, 4, 22), repo_root=repo)
    assert "Soul content." in out
    assert "jeff@clarkle.com" in out
    assert DAY_1_HISTORY_SENTINEL in out
    assert DAY_1_FEEDBACK_SENTINEL in out
    assert "Today is 2026-04-22." in out
    assert "<site>" in out and "<log>" in out


def test_assemble_prompt_with_feedback_file(tmp_path):
    repo = _make_fake_repo(tmp_path)
    (repo / "archive" / "2026-04-21.html").write_text("<html></html>")
    feedback_file = repo / "feedback" / "2026-04-21.json"
    feedback_file.write_text(json.dumps(_full_feedback()))
    out = assemble_prompt(date(2026, 4, 22), repo_root=repo)
    assert "142 visitors" in out
    assert "Yesterday's feedback (2026-04-21):" in out
    # Day-1 sentinel must NOT appear when feedback present
    assert DAY_1_FEEDBACK_SENTINEL not in out


def test_assemble_prompt_five_recent_no_older(tmp_path):
    repo = _make_fake_repo(tmp_path)
    today = date(2026, 4, 22)
    for i in range(5):
        _write_log(repo / "log", today - timedelta(days=i + 1), importance=3, body=f"body-{i}")
    out = assemble_prompt(today, repo_root=repo)
    assert "Recent history" in out
    assert "Older" not in out
    for i in range(5):
        assert f"body-{i}" in out


def test_cli_main_prints_prompt_via_real_repo(capsys, monkeypatch, tmp_path):
    """Smoke-test main() end-to-end by redirecting REPO_ROOT to a fresh fake repo."""
    repo = _make_fake_repo(tmp_path)
    import scripts.assemble_prompt as mod

    monkeypatch.setattr(mod, "REPO_ROOT", repo)
    rc = mod.main(["--date", "2026-01-01"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Soul content." in out
    assert "jeff@clarkle.com" in out
    assert DAY_1_HISTORY_SENTINEL in out
    assert DAY_1_FEEDBACK_SENTINEL in out
    assert "Today is 2026-01-01." in out
