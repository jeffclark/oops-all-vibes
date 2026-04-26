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
    """Return the JSON body of /stats/total for [start, end] (inclusive), or None on any error.

    GoatCounter's `start` and `end` parameters are full timestamps, not bare
    dates. Sending bare ISO dates makes `start=end=YYYY-MM-DD` a zero-width
    window (midnight to midnight) and the call returns 0 every time. We send
    explicit UTC bookends so the range covers the full inclusive date span.
    """
    url = f"{base}{STATS_TOTAL_ENDPOINT}"
    try:
        response = session.get(
            url,
            params={
                "start": f"{start.isoformat()}T00:00:00Z",
                "end": f"{end.isoformat()}T23:59:59Z",
            },
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
    """Return {ISO_DATE: visitor_count} for each day in [start, end] that the API answered for.

    Reads `total_utc` rather than `total` so the per-day buckets align with the
    UTC date keys we emit (and with the UTC `run_date` math elsewhere). `total`
    in the response is bucketed by the site's configured timezone, which would
    cause EST evening visits to land in a different day than our query asked for.
    """
    result: dict[str, int] = {}
    cursor = start
    while cursor <= end:
        data = _fetch_total(session, base, cursor, cursor)
        if data is not None:
            visitors = data.get("total_utc")
            if visitors is not None:
                result[cursor.isoformat()] = visitors
        cursor += timedelta(days=1)
    return result


def _peak_from_series(series: dict[str, int]) -> dict | None:
    if not series:
        return None
    peak_date = max(series, key=series.__getitem__)
    peak_visitors = series[peak_date]
    if peak_visitors <= 0:
        return None
    return {"date": peak_date, "visitors": peak_visitors}


def _earliest_archive_date(archive_entries: list[Path]) -> date | None:
    """Return the earliest YYYY-MM-DD date parsed from archive filenames, or None."""
    parsed: list[date] = []
    for entry in archive_entries:
        try:
            parsed.append(date.fromisoformat(entry.stem))
        except ValueError:
            continue
    return min(parsed) if parsed else None


def _pct_change(current: int | None, previous: int | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator, 2)


def _read_jeff_note(notes_dir: Path, date_iso: str) -> str | None:
    """Return the trimmed contents of notes/<date>.md, or None if absent/empty.

    This is how Jeff talks to Georgia. One file per note, dated to match the
    feedback day (same as the feedback JSON filename). Empty files are ignored
    so Jeff can clear an unsent note without deleting it.
    """
    path = notes_dir / f"{date_iso}.md"
    if not path.is_file():
        return None
    try:
        content = path.read_text().strip()
    except OSError as exc:
        _warn(f"failed to read {path}: {exc}")
        return None
    return content if content else None


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
    # All-time: bound to the site's actual lifetime, ending yesterday. A 2020→today
    # span used to silently fail (oversized range and a still-in-progress end date),
    # leaving Georgia without a total to anchor her narrative.
    all_time_start = _earliest_archive_date(archive_entries) or yesterday
    all_time = _fetch_total(session, base, all_time_start, yesterday) or {}

    # Per-day series across the site's lifetime (capped). Powers both peak detection
    # and the per-day breakdown Georgia uses to reconcile rolling vs daily totals.
    days_to_scan = min(PEAK_SCAN_DAYS, len(archive_entries))
    series_start = yesterday - timedelta(days=days_to_scan - 1)
    daily_series = _fetch_per_day_totals(session, base, series_start, yesterday)
    peak_day = _peak_from_series(daily_series)

    yesterday_visitors = day.get("total_utc")
    # GoatCounter's /stats/total is visitor-centric; it does not expose a separate
    # pageview count. Leave pageviews null so the feedback narrative simply omits it.
    # A future refinement can aggregate /stats/hits to recover pageviews if desired.
    yesterday_pageviews = None
    l7_visitors = last_7.get("total_utc")
    l7_avg = round(l7_visitors / 7, 2) if l7_visitors is not None else None
    l30_visitors = last_30.get("total_utc")
    l30_avg = round(l30_visitors / 30, 2) if l30_visitors is not None else None
    prev7_visitors = prev_7.get("total_utc")

    jeff_note = _read_jeff_note(root / "notes", yesterday.isoformat())

    # Freshness check: GoatCounter's per-day aggregates can lag the rolling totals
    # by ~1 day, especially for the just-ended day. When the site is younger than
    # the 7-day window and the per-day sum disagrees with the 7-day total, surface
    # that explicitly so Georgia doesn't write "zero yesterday" alongside "26 in
    # the week" without acknowledging the gap.
    data_freshness_note: str | None = None
    if (
        l7_visitors is not None
        and daily_series
        and len(archive_entries) < 7
        and sum(daily_series.values()) != l7_visitors
    ):
        data_freshness_note = (
            "GoatCounter's per-day totals can lag the rolling totals by ~1 day; "
            "trust the cumulative number more than today's per-day breakdown."
        )

    # If every API call failed AND there's no note, treat as total fetch failure.
    # A note alone is enough to justify writing the feedback file — Georgia should
    # always see Jeff's message when he leaves one.
    any_data = any(
        v is not None
        for v in (
            yesterday_visitors,
            yesterday_pageviews,
            l7_visitors,
            l30_visitors,
            all_time.get("total_utc"),
            prev7_visitors,
            peak_day,
            jeff_note,
        )
    )
    if not any_data:
        _warn("no data returned from GoatCounter and no note; skipping feedback write")
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
            "all_time_visitors": all_time.get("total_utc"),
            "days_live": len(archive_entries),
            "peak_day": peak_day,
        },
        "trend": {
            "yesterday_vs_7d_avg": _ratio(yesterday_visitors, l7_avg),
            "week_over_week_pct": _pct_change(l7_visitors, prev7_visitors),
        },
        "days_live_series": daily_series,
        "data_freshness_note": data_freshness_note,
        "jeff_note": jeff_note,
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
