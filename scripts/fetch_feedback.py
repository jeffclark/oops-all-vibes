"""Fetch yesterday's visitor stats from GoatCounter and write the Layer 4 JSON.

Resilient by design: missing env vars, missing archive, and any API error all
result in "no file written" and a graceful return. The assembly layer falls
back to the no-feedback sentinel in those cases.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


REPO_ROOT = Path(__file__).resolve().parent.parent

API_BASE_TEMPLATE = "https://{code}.goatcounter.com/api/v0"
STATS_TOTAL_ENDPOINT = "/stats/total"
REQUEST_TIMEOUT_S = 30
PEAK_SCAN_DAYS = 60


def _warn(msg: str) -> None:
    print(f"fetch_feedback: {msg}", file=sys.stderr)


def _fetch_total(
    session: requests.Session,
    base: str,
    start: date,
    end: date,
) -> dict | None:
    """Return the JSON body of /stats/total for [start, end], or None on any error."""
    url = f"{base}{STATS_TOTAL_ENDPOINT}"
    try:
        response = session.get(
            url,
            params={"start": start.isoformat(), "end": end.isoformat()},
            timeout=REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001 — defensive: never let anything crash the pipeline
        _warn(f"{url} {start}..{end}: {exc}")
        return None


def _fetch_per_day_totals(
    session: requests.Session,
    base: str,
    start: date,
    end: date,
) -> dict[str, int]:
    """Return {ISO_DATE: visitor_count} for each day in [start, end] that the API answered for."""
    result: dict[str, int] = {}
    cursor = start
    while cursor <= end:
        data = _fetch_total(session, base, cursor, cursor)
        if data is not None:
            visitors = data.get("total_unique")
            if visitors is not None:
                result[cursor.isoformat()] = visitors
        cursor += timedelta(days=1)
    return result


def _peak_from_series(series: dict[str, int]) -> dict | None:
    if not series:
        return None
    peak_date = max(series, key=series.__getitem__)
    return {"date": peak_date, "visitors": series[peak_date]}


def _pct_change(current: int | None, previous: int | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator, 2)


def fetch_feedback(run_date: date, repo_root: Path | None = None) -> dict | None:
    root = repo_root or REPO_ROOT
    api_key = os.environ.get("GOATCOUNTER_API_KEY")
    code = os.environ.get("GOATCOUNTER_CODE")
    if not api_key or not code:
        _warn("GOATCOUNTER_API_KEY or GOATCOUNTER_CODE not set; skipping")
        return None

    archive = root / "archive"
    archive_entries = (
        [p for p in archive.glob("*.html") if p.name != "index.html"]
        if archive.is_dir()
        else []
    )
    if not archive_entries:
        _warn("archive/ has no prior entries (day 1); skipping")
        return None

    yesterday = run_date - timedelta(days=1)
    base = API_BASE_TEMPLATE.format(code=code)
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {api_key}"

    day = _fetch_total(session, base, yesterday, yesterday) or {}
    seven_start = yesterday - timedelta(days=6)
    last_7 = _fetch_total(session, base, seven_start, yesterday) or {}
    thirty_start = yesterday - timedelta(days=29)
    last_30 = _fetch_total(session, base, thirty_start, yesterday) or {}
    prev_seven_end = yesterday - timedelta(days=7)
    prev_seven_start = yesterday - timedelta(days=13)
    prev_7 = _fetch_total(session, base, prev_seven_start, prev_seven_end) or {}
    all_time_start = date(2020, 1, 1)
    all_time = _fetch_total(session, base, all_time_start, run_date) or {}

    # Peak day: scan the recent window. Capped so the run doesn't balloon on long-running sites.
    days_to_scan = min(PEAK_SCAN_DAYS, len(archive_entries))
    peak_scan_start = yesterday - timedelta(days=days_to_scan - 1)
    daily_series = _fetch_per_day_totals(session, base, peak_scan_start, yesterday)
    peak_day = _peak_from_series(daily_series)

    yesterday_visitors = day.get("total_unique")
    yesterday_pageviews = day.get("total")
    l7_visitors = last_7.get("total_unique")
    l7_avg = round(l7_visitors / 7, 2) if l7_visitors is not None else None
    l30_visitors = last_30.get("total_unique")
    l30_avg = round(l30_visitors / 30, 2) if l30_visitors is not None else None
    prev7_visitors = prev_7.get("total_unique")

    # If every API call failed, treat as total fetch failure — don't write a file.
    # The assembler's no-feedback sentinel will fire instead.
    any_data = any(
        v is not None
        for v in (
            yesterday_visitors,
            yesterday_pageviews,
            l7_visitors,
            l30_visitors,
            all_time.get("total_unique"),
            prev7_visitors,
            peak_day,
        )
    )
    if not any_data:
        _warn("no data returned from GoatCounter; skipping feedback write")
        return None

    result = {
        "date": yesterday.isoformat(),
        "yesterday": {
            "visitors": yesterday_visitors,
            "pageviews": yesterday_pageviews,
        },
        "recent": {
            "last_7_days_visitors": l7_visitors,
            "last_7_days_avg": l7_avg,
            "last_30_days_visitors": l30_visitors,
            "last_30_days_avg": l30_avg,
        },
        "historical": {
            "all_time_visitors": all_time.get("total_unique"),
            "days_live": len(archive_entries),
            "peak_day": peak_day,
        },
        "trend": {
            "yesterday_vs_7d_avg": _ratio(yesterday_visitors, l7_avg),
            "week_over_week_pct": _pct_change(l7_visitors, prev7_visitors),
        },
        "jeff_note": None,
    }

    output_dir = root / "feedback"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{yesterday.isoformat()}.json"
    tmp = output_file.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(result, indent=2))
        tmp.replace(output_file)
    except Exception as exc:  # noqa: BLE001
        _warn(f"failed to write {output_file}: {exc}")
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        return None

    return result


def main(argv: list[str] | None = None) -> int:
    run_date = datetime.now(timezone.utc).date()
    try:
        fetch_feedback(run_date)
    except Exception as exc:  # noqa: BLE001 — last-resort safety net
        _warn(f"unexpected error: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
