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
    """Canned /stats/total responses for a known scenario."""
    # 30-day peak scan range
    scan_start = YESTERDAY - timedelta(days=29)
    responses: dict[tuple[str, str], dict] = {
        # aggregate periods
        (YESTERDAY.isoformat(), YESTERDAY.isoformat()): {"total": 289, "total_unique": 142},
        ((YESTERDAY - timedelta(days=6)).isoformat(), YESTERDAY.isoformat()): {"total_unique": 487},
        ((YESTERDAY - timedelta(days=29)).isoformat(), YESTERDAY.isoformat()): {"total_unique": 1204},
        ((YESTERDAY - timedelta(days=13)).isoformat(), (YESTERDAY - timedelta(days=7)).isoformat()): {"total_unique": 369},
        (date(2020, 1, 1).isoformat(), RUN_DATE.isoformat()): {"total_unique": 8430},
    }
    # per-day peak scan: make day D-14 be the peak
    peak_date = YESTERDAY - timedelta(days=14)
    d = scan_start
    while d <= YESTERDAY:
        # Don't overwrite the aggregate-period single-day already set above
        if (d.isoformat(), d.isoformat()) in responses:
            d += timedelta(days=1)
            continue
        v = 512 if d == peak_date else 50
        responses[(d.isoformat(), d.isoformat())] = {"total_unique": v}
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
    assert result["yesterday"] == {"visitors": 142, "pageviews": 289}
    assert result["recent"]["last_7_days_visitors"] == 487
    assert result["recent"]["last_7_days_avg"] == round(487 / 7, 2)
    assert result["recent"]["last_30_days_visitors"] == 1204
    assert result["recent"]["last_30_days_avg"] == round(1204 / 30, 2)
    assert result["historical"]["all_time_visitors"] == 8430
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
            resp.json.return_value = {"total": 289, "total_unique": 142}
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
