"""Tests for scripts/fetch_feedback.py.

All HTTP calls are mocked by substituting a fake session whose `get` returns
canned responses keyed by the request's (start, end) params.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import scripts.fetch_feedback as ff  # noqa: E402


RUN_DATE = date(2026, 4, 22)
YESTERDAY = date(2026, 4, 21)


def _make_repo(tmp_path: Path, archive_count: int = 3) -> Path:
    (tmp_path / "archive").mkdir()
    (tmp_path / "feedback").mkdir()
    for i in range(archive_count):
        d = YESTERDAY - timedelta(days=i)
        (tmp_path / "archive" / f"{d.isoformat()}.html").write_text("<html></html>")
    return tmp_path


def _fake_session(response_map: dict[tuple[str, str], dict | int]):
    """Returns a MagicMock session whose .get(url, params=...) consults response_map.

    Keys are (start, end) ISO strings. Values are either a dict (JSON body, 200)
    or an int (HTTP status to return with raise_for_status triggered).
    """
    session = MagicMock()
    session.headers = {}

    def fake_get(url, params=None, timeout=None):
        key = (params["start"], params["end"])
        value = response_map.get(key)
        resp = MagicMock()
        if isinstance(value, int):
            resp.status_code = value
            resp.raise_for_status.side_effect = Exception(f"HTTP {value}")
            return resp
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = value if value is not None else {}
        return resp

    session.get.side_effect = fake_get
    return session


# ---------- env var guards ----------


def test_missing_api_key_returns_none_no_file(monkeypatch, tmp_path, capsys):
    repo = _make_repo(tmp_path)
    monkeypatch.delenv("GOATCOUNTER_API_KEY", raising=False)
    monkeypatch.setenv("GOATCOUNTER_CODE", "oops-all-vibes")
    assert ff.fetch_feedback(RUN_DATE, repo_root=repo) is None
    assert not any((repo / "feedback").iterdir())
    assert "GOATCOUNTER_API_KEY" in capsys.readouterr().err


def test_missing_code_returns_none_no_file(monkeypatch, tmp_path, capsys):
    repo = _make_repo(tmp_path)
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.delenv("GOATCOUNTER_CODE", raising=False)
    assert ff.fetch_feedback(RUN_DATE, repo_root=repo) is None
    assert "GOATCOUNTER_CODE" in capsys.readouterr().err


# ---------- day 1 short-circuit ----------


def test_day_1_archive_empty_returns_none(monkeypatch, tmp_path, capsys):
    tmp_path_with_empty = tmp_path
    (tmp_path_with_empty / "archive").mkdir()
    (tmp_path_with_empty / "feedback").mkdir()
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "oops-all-vibes")
    assert ff.fetch_feedback(RUN_DATE, repo_root=tmp_path_with_empty) is None
    assert "day 1" in capsys.readouterr().err
    assert not any((tmp_path_with_empty / "feedback").iterdir())


# ---------- HTTP error ----------


def test_http_500_no_file_written(monkeypatch, tmp_path, capsys):
    repo = _make_repo(tmp_path)
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "oops-all-vibes")

    def always_500(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("HTTP 500")
        return resp

    session = MagicMock()
    session.headers = {}
    session.get.side_effect = always_500
    monkeypatch.setattr(ff.requests, "Session", lambda: session)

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result is None
    assert not any((repo / "feedback").iterdir())
    err = capsys.readouterr().err
    assert "HTTP 500" in err
    assert "no data" in err


# ---------- happy path with canned data ----------


def _happy_responses() -> dict[tuple[str, str], dict]:
    """Canned /stats/total responses for a known scenario.

    Field name `total` matches the real GoatCounter schema: it's visitor count
    (including events), not pageviews.
    """
    scan_start = YESTERDAY - timedelta(days=29)
    responses: dict[tuple[str, str], dict] = {
        (YESTERDAY.isoformat(), YESTERDAY.isoformat()): {"total": 142},
        ((YESTERDAY - timedelta(days=6)).isoformat(), YESTERDAY.isoformat()): {"total": 487},
        # 30-day call AND all-time call (archive = 30 days) hit this same key.
        ((YESTERDAY - timedelta(days=29)).isoformat(), YESTERDAY.isoformat()): {"total": 1204},
        ((YESTERDAY - timedelta(days=13)).isoformat(), (YESTERDAY - timedelta(days=7)).isoformat()): {"total": 369},
    }
    # per-day peak scan: make day D-14 be the peak
    peak_date = YESTERDAY - timedelta(days=14)
    d = scan_start
    while d <= YESTERDAY:
        if (d.isoformat(), d.isoformat()) in responses:
            d += timedelta(days=1)
            continue
        v = 512 if d == peak_date else 50
        responses[(d.isoformat(), d.isoformat())] = {"total": v}
        d += timedelta(days=1)
    return responses


def test_happy_path_produces_full_schema(monkeypatch, tmp_path):
    # archive must have at least 30 days so the peak scan actually covers 30 days
    repo = tmp_path
    (repo / "archive").mkdir()
    (repo / "feedback").mkdir()
    for i in range(30):
        d = YESTERDAY - timedelta(days=i)
        (repo / "archive" / f"{d.isoformat()}.html").write_text("<html></html>")

    responses = _happy_responses()
    session = _fake_session(responses)
    monkeypatch.setattr(ff.requests, "Session", lambda: session)
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "oops-all-vibes")

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result is not None

    # File exists and matches returned dict
    file_content = json.loads((repo / "feedback" / f"{YESTERDAY.isoformat()}.json").read_text())
    assert file_content == result

    # Schema fields
    assert result["date"] == YESTERDAY.isoformat()
    # pageviews intentionally null — GoatCounter /stats/total is visitor-centric
    assert result["yesterday"] == {"visitors": 142, "pageviews": None}
    assert result["recent"]["last_7_days_visitors"] == 487
    assert result["recent"]["last_7_days_avg"] == round(487 / 7, 2)
    assert result["recent"]["last_30_days_visitors"] == 1204
    assert result["recent"]["last_30_days_avg"] == round(1204 / 30, 2)
    # All-time is bounded by archive's earliest date (here 30 days back) and
    # yesterday — same window as the 30-day call, so same total.
    assert result["historical"]["all_time_visitors"] == 1204
    assert result["historical"]["days_live"] == 30
    assert result["historical"]["peak_day"] is not None
    # Peak must identify the D-14 day with 512 visitors
    expected_peak_date = (YESTERDAY - timedelta(days=14)).isoformat()
    assert result["historical"]["peak_day"]["date"] == expected_peak_date
    assert result["historical"]["peak_day"]["visitors"] == 512
    # Trend: y_vs_7d_avg = 142 / (487/7) ≈ 2.04
    assert result["trend"]["yesterday_vs_7d_avg"] == pytest.approx(142 / round(487 / 7, 2), abs=0.02)
    # WoW: (487 - 369) / 369 * 100 = ~31.98%
    assert result["trend"]["week_over_week_pct"] == pytest.approx((487 - 369) / 369 * 100, abs=0.2)
    assert result["jeff_note"] is None
    # Per-day series spans the scan window (30 days, capped by archive count)
    series = result["days_live_series"]
    assert len(series) == 30
    assert series[YESTERDAY.isoformat()] == 142
    assert series[(YESTERDAY - timedelta(days=14)).isoformat()] == 512
    # Site is older than 7 days, so no freshness note even if numbers don't reconcile.
    assert result["data_freshness_note"] is None


# ---------- insufficient-data nulls ----------


def test_wow_null_when_prev_week_unknown(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path, archive_count=1)
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "oops-all-vibes")

    session = MagicMock()
    session.headers = {}

    def fake_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        # Only answer yesterday's single-day query; everything else → 500
        if params["start"] == YESTERDAY.isoformat() and params["end"] == YESTERDAY.isoformat():
            resp.json.return_value = {"total": 142}
        else:
            resp.raise_for_status.side_effect = Exception("HTTP 500")
        return resp

    session.get.side_effect = fake_get
    monkeypatch.setattr(ff.requests, "Session", lambda: session)

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result is not None
    assert result["trend"]["week_over_week_pct"] is None
    assert result["trend"]["yesterday_vs_7d_avg"] is None
    # Peak has only yesterday's entry
    assert result["historical"]["peak_day"] == {"date": YESTERDAY.isoformat(), "visitors": 142}


def test_main_wraps_exceptions(monkeypatch):
    def boom(run_date):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ff, "fetch_feedback", boom)
    rc = ff.main([])
    assert rc == 0


# ---------- jeff_note from notes/<date>.md ----------


def _happy_archive(tmp_path: Path, n: int = 3) -> Path:
    (tmp_path / "archive").mkdir()
    (tmp_path / "feedback").mkdir()
    (tmp_path / "notes").mkdir()
    for i in range(n):
        d = YESTERDAY - timedelta(days=i)
        (tmp_path / "archive" / f"{d.isoformat()}.html").write_text("<html></html>")
    return tmp_path


def _session_with_happy_data(monkeypatch):
    """Return a session that answers any /stats/total call with a small non-null total."""
    session = MagicMock()
    session.headers = {}

    def fake_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"total": 10}
        return resp

    session.get.side_effect = fake_get
    monkeypatch.setattr(ff.requests, "Session", lambda: session)
    return session


def test_jeff_note_included_when_file_exists(monkeypatch, tmp_path):
    repo = _happy_archive(tmp_path)
    (repo / "notes" / f"{YESTERDAY.isoformat()}.md").write_text("the terminal-green day was best. more of that.\n")
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "clarkle")
    _session_with_happy_data(monkeypatch)

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result is not None
    assert result["jeff_note"] == "the terminal-green day was best. more of that."


def test_jeff_note_null_when_file_absent(monkeypatch, tmp_path):
    repo = _happy_archive(tmp_path)
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "clarkle")
    _session_with_happy_data(monkeypatch)

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result is not None
    assert result["jeff_note"] is None


def test_jeff_note_null_when_file_empty(monkeypatch, tmp_path):
    repo = _happy_archive(tmp_path)
    (repo / "notes" / f"{YESTERDAY.isoformat()}.md").write_text("")
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "clarkle")
    _session_with_happy_data(monkeypatch)

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result["jeff_note"] is None


def test_jeff_note_null_when_file_whitespace_only(monkeypatch, tmp_path):
    repo = _happy_archive(tmp_path)
    (repo / "notes" / f"{YESTERDAY.isoformat()}.md").write_text("   \n\n  \n")
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "clarkle")
    _session_with_happy_data(monkeypatch)

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result["jeff_note"] is None


def test_note_alone_is_enough_to_write_file(monkeypatch, tmp_path, capsys):
    """If the API is fully dark but a note exists, the feedback file still gets written."""
    repo = _happy_archive(tmp_path)
    (repo / "notes" / f"{YESTERDAY.isoformat()}.md").write_text("tell me what you think about the archive so far.")
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "clarkle")

    def always_500(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("HTTP 500")
        return resp

    session = MagicMock()
    session.headers = {}
    session.get.side_effect = always_500
    monkeypatch.setattr(ff.requests, "Session", lambda: session)

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result is not None
    assert result["yesterday"]["visitors"] is None
    assert result["jeff_note"] == "tell me what you think about the archive so far."
    # File exists on disk
    written = (repo / "feedback" / f"{YESTERDAY.isoformat()}.json").read_text()
    assert "tell me what you think" in written


def test_note_file_with_trailing_whitespace_is_trimmed(monkeypatch, tmp_path):
    repo = _happy_archive(tmp_path)
    (repo / "notes" / f"{YESTERDAY.isoformat()}.md").write_text("  short note  \n\n")
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "clarkle")
    _session_with_happy_data(monkeypatch)

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result["jeff_note"] == "short note"


# ---------- peak / all-time / freshness ----------


def test_peak_day_none_when_all_zero():
    assert ff._peak_from_series({"2026-04-23": 0, "2026-04-24": 0}) is None


def test_peak_day_skips_zero_when_only_day():
    assert ff._peak_from_series({"2026-04-24": 0}) is None


def test_peak_day_returned_when_any_positive():
    assert ff._peak_from_series({"2026-04-23": 0, "2026-04-24": 5}) == {
        "date": "2026-04-24",
        "visitors": 5,
    }


def test_all_time_call_uses_archive_earliest_and_yesterday(monkeypatch, tmp_path):
    """The all-time fetch must be bounded by the archive's earliest date and
    yesterday — not 2020→today. The old wide range silently returned null."""
    repo = _make_repo(tmp_path, archive_count=3)  # YESTERDAY, Y-1, Y-2
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "clarkle")

    seen_calls: list[tuple[str, str]] = []

    def fake_get(url, params=None, timeout=None):
        seen_calls.append((params["start"], params["end"]))
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"total": 7}
        return resp

    session = MagicMock()
    session.headers = {}
    session.get.side_effect = fake_get
    monkeypatch.setattr(ff.requests, "Session", lambda: session)

    ff.fetch_feedback(RUN_DATE, repo_root=repo)

    earliest = (YESTERDAY - timedelta(days=2)).isoformat()
    assert (earliest, YESTERDAY.isoformat()) in seen_calls
    # The old broken range must NOT be queried
    assert (date(2020, 1, 1).isoformat(), RUN_DATE.isoformat()) not in seen_calls


def test_freshness_note_set_when_per_day_disagrees_with_l7(monkeypatch, tmp_path):
    """Young site with 7-day total > sum of per-day totals → freshness note."""
    repo = _make_repo(tmp_path, archive_count=2)  # YESTERDAY, Y-1
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "clarkle")

    seven_start = YESTERDAY - timedelta(days=6)

    def fake_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        s, e = params["start"], params["end"]
        if s == seven_start.isoformat() and e == YESTERDAY.isoformat():
            resp.json.return_value = {"total": 26}
        else:
            resp.json.return_value = {"total": 0}
        return resp

    session = MagicMock()
    session.headers = {}
    session.get.side_effect = fake_get
    monkeypatch.setattr(ff.requests, "Session", lambda: session)

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result is not None
    assert result["recent"]["last_7_days_visitors"] == 26
    assert sum(result["days_live_series"].values()) == 0
    assert result["data_freshness_note"] is not None
    assert "lag" in result["data_freshness_note"]


def test_freshness_note_none_when_numbers_reconcile(monkeypatch, tmp_path):
    """Per-day sum matches l7 → no freshness note."""
    repo = _make_repo(tmp_path, archive_count=2)
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "clarkle")

    seven_start = YESTERDAY - timedelta(days=6)

    def fake_get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        s, e = params["start"], params["end"]
        if s == seven_start.isoformat() and e == YESTERDAY.isoformat():
            resp.json.return_value = {"total": 20}
        elif s == e:
            # Per-day calls — split 20 across the 2 archive days, 0 elsewhere.
            if s == YESTERDAY.isoformat():
                resp.json.return_value = {"total": 12}
            elif s == (YESTERDAY - timedelta(days=1)).isoformat():
                resp.json.return_value = {"total": 8}
            else:
                resp.json.return_value = {"total": 0}
        else:
            resp.json.return_value = {"total": 0}
        return resp

    session = MagicMock()
    session.headers = {}
    session.get.side_effect = fake_get
    monkeypatch.setattr(ff.requests, "Session", lambda: session)

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result is not None
    # 7-day total matches sum of per-day series → no freshness note.
    assert result["data_freshness_note"] is None


def test_freshness_note_none_when_site_is_old_enough(monkeypatch, tmp_path):
    """Site ≥7 days → no freshness note even if numbers don't reconcile."""
    repo = _make_repo(tmp_path, archive_count=10)
    monkeypatch.setenv("GOATCOUNTER_API_KEY", "x")
    monkeypatch.setenv("GOATCOUNTER_CODE", "clarkle")
    _session_with_happy_data(monkeypatch)  # every call returns 10

    result = ff.fetch_feedback(RUN_DATE, repo_root=repo)
    assert result is not None
    assert result["data_freshness_note"] is None
