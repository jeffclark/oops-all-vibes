"""Probe the candidate daily-input sources and report what Georgia would actually see.

This is a diagnostic, not part of the daily pipeline. It answers one question:
for each candidate input, can we fetch it from a datacenter IP without an API
key, and what does today's payload look like?

Every probe is individually defensive — a source that 403s, times out, or
changes shape reports a failure and the rest keep going. That mirrors how the
real fetchers would have to behave: a dead input is content, not an outage.

    python scripts/probe_inputs.py            # human report
    python scripts/probe_inputs.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import requests

TIMEOUT_S = 30
UA = "clarkle.com daily-input probe (jeff@clarkle.com)"


@dataclass
class Probe:
    name: str
    ok: bool = False
    status: int | None = None
    latency_ms: int | None = None
    url: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str = ""


def _get(session: requests.Session, url: str, **kw) -> tuple[requests.Response | None, int, str]:
    """GET a URL, returning (response, elapsed_ms, error_string)."""
    start = time.time()
    try:
        r = session.get(url, timeout=TIMEOUT_S, **kw)
        return r, int((time.time() - start) * 1000), ""
    except Exception as exc:  # noqa: BLE001 — a probe must never raise
        return None, int((time.time() - start) * 1000), f"{type(exc).__name__}: {exc}"


def _blocked_reason(r: requests.Response) -> str:
    """Name the anti-bot wall, if the response looks like one."""
    body = (r.text or "")[:4000].lower()
    if r.status_code in (401, 403):
        if "cloudflare" in body or "cf-ray" in {k.lower() for k in r.headers}:
            return "Cloudflare challenge"
        return f"HTTP {r.status_code}"
    if "captcha" in body or "are you a human" in body:
        return "CAPTCHA interstitial"
    if r.status_code == 429:
        return "rate limited"
    return ""


# --------------------------------------------------------------------------
# 1. FSA photograph — Library of Congress JSON API
# --------------------------------------------------------------------------
FSA_COLLECTION = "https://www.loc.gov/collections/fsa-owi-black-and-white-negatives/"


def probe_fsa(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="FSA photograph (Library of Congress)")
    # Page deep into the collection to get an arbitrary item rather than the
    # same first-page results every day.
    page = rng.randint(1, 500)
    p.url = f"{FSA_COLLECTION}?fo=json&c=25&sp={page}&at=results,pagination"
    r, ms, err = _get(session, p.url)
    p.latency_ms = ms
    if r is None:
        p.error = err
        return p
    p.status = r.status_code
    reason = _blocked_reason(r)
    if reason:
        p.error = reason
        return p
    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        p.error = f"not JSON ({exc}); likely an HTML interstitial"
        return p

    results = data.get("results") or []
    if not results:
        p.error = f"no results at sp={page} (deep paging may be capped)"
        p.notes.append(f"pagination: {json.dumps(data.get('pagination', {}))[:300]}")
        return p

    item = rng.choice(results)
    image_urls = item.get("image_url") or []
    p.ok = True
    p.payload = {
        "title": item.get("title"),
        "date": item.get("date"),
        "created_published": item.get("created_published"),
        "subject": item.get("subject"),
        "location": item.get("location"),
        "id": item.get("id"),
        "image_url": image_urls[-1] if image_urls else None,
        "image_count": len(image_urls),
    }
    p.notes.append(f"collection total: {data.get('pagination', {}).get('of', 'unknown')}")
    p.notes.append("no API key; documented limit is 20 req/min, 1-hour ban if exceeded")
    return p


# --------------------------------------------------------------------------
# 2. Craigslist free stuff
# --------------------------------------------------------------------------
CL_CANDIDATES = [
    "https://boston.craigslist.org/search/zip?format=rss",
    "https://boston.craigslist.org/search/sob/zip?format=rss",  # south shore
    "https://boston.craigslist.org/search/zip",
]


def probe_craigslist(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Craigslist free stuff (Boston)")
    attempts = []
    for url in CL_CANDIDATES:
        r, ms, err = _get(session, url)
        if r is None:
            attempts.append(f"{url} -> {err}")
            continue
        attempts.append(f"{url} -> HTTP {r.status_code} ({len(r.content)} bytes, {ms}ms)")
        p.url, p.status, p.latency_ms = url, r.status_code, ms
        reason = _blocked_reason(r)
        if reason:
            attempts[-1] += f" [{reason}]"
            continue
        if r.status_code != 200:
            continue
        items = re.findall(r"<title>(.*?)</title>", r.text, re.S)
        items = [i.strip() for i in items if i.strip()][1:]  # drop channel title
        if items:
            p.ok = True
            p.payload = {"sample_titles": items[:5], "item_count": len(items)}
            p.notes = attempts
            return p
        attempts[-1] += " [200 but no parseable items — JS shell]"
    p.notes = attempts
    p.error = "no candidate URL returned usable listings"
    return p


# --------------------------------------------------------------------------
# 3. Boston Globe corrections
# --------------------------------------------------------------------------
GLOBE_CANDIDATES = [
    "https://www.bostonglobe.com/corrections/",
    "https://www.bostonglobe.com/metro/corrections/",
    "https://www.bostonglobe.com/rss/feedRiverMetro",
    "http://archive.boston.com/bostonglobe/corrections/",
]


def probe_globe(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Boston Globe corrections")
    attempts = []
    for url in GLOBE_CANDIDATES:
        r, ms, err = _get(session, url, allow_redirects=True)
        if r is None:
            attempts.append(f"{url} -> {err}")
            continue
        reason = _blocked_reason(r)
        attempts.append(
            f"{url} -> HTTP {r.status_code} ({len(r.content)} bytes, {ms}ms)"
            + (f" [{reason}]" if reason else "")
        )
        p.url, p.status, p.latency_ms = url, r.status_code, ms
        if r.status_code == 200 and not reason:
            text = re.sub(r"<script.*?</script>", " ", r.text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            hit = re.search(r"(correction|clarification)[^.]{0,400}\.", text, re.I)
            p.ok = bool(hit)
            p.payload = {"excerpt": hit.group(0)[:400] if hit else None,
                         "text_chars": len(text)}
            if p.ok:
                p.notes = attempts
                return p
            attempts[-1] += " [200 but no corrections text found]"
    p.notes = attempts
    p.error = "no reachable corrections source"
    return p


# --------------------------------------------------------------------------
# 4. Oklahoma State hockey (ACHA)
# --------------------------------------------------------------------------
OKST_CANDIDATES = [
    "https://www.okstatehockey.com/schedule/upcoming",
    "https://www.okstatehockey.com/events",
    "https://www.eliteprospects.com/team/35165/oklahoma-state-univ",
]


def probe_okstate(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Oklahoma State hockey (ACHA)")
    attempts = []
    for url in OKST_CANDIDATES:
        r, ms, err = _get(session, url)
        if r is None:
            attempts.append(f"{url} -> {err}")
            continue
        reason = _blocked_reason(r)
        attempts.append(
            f"{url} -> HTTP {r.status_code} ({len(r.content)} bytes, {ms}ms)"
            + (f" [{reason}]" if reason else "")
        )
        p.url, p.status, p.latency_ms = url, r.status_code, ms
        if r.status_code != 200 or reason:
            continue
        text = re.sub(r"<script.*?</script>", " ", r.text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        # Look for anything score- or date-shaped.
        scores = re.findall(r"\b\d{1,2}\s*[-–]\s*\d{1,2}\b", text)
        dates = re.findall(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b", text
        )
        if scores or dates:
            p.ok = True
            p.payload = {
                "score_shaped_strings": scores[:8],
                "date_shaped_strings": dates[:8],
                "text_chars": len(text),
            }
            p.notes = attempts
            return p
        attempts[-1] += " [200 but no schedule/score text — likely JS-rendered]"
    p.notes = attempts
    p.error = "no reachable schedule/results source"
    return p


# --------------------------------------------------------------------------
# 5. Patent granted (USPTO)
# --------------------------------------------------------------------------
def _last_grant_tuesday(today: date) -> date:
    """US patents issue on Tuesdays. Return the most recent one."""
    return today - timedelta(days=(today.weekday() - 1) % 7)


def probe_patent(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Patent granted (USPTO)")
    tuesday = _last_grant_tuesday(datetime.now(timezone.utc).date())
    p.payload["most_recent_issue_day"] = tuesday.isoformat()
    attempts = []

    # a) Open Data Portal — expected to demand an API key.
    odp = "https://api.uspto.gov/api/v1/patent/applications/search?q=*&limit=1"
    r, ms, err = _get(session, odp)
    if r is None:
        attempts.append(f"ODP {odp} -> {err}")
    else:
        attempts.append(f"ODP -> HTTP {r.status_code} ({ms}ms) {r.text[:160]!r}")

    # b) Bulk weekly grant XML — no key, but very large files.
    bulk = "https://bulkdata.uspto.gov/data/patent/grant/redbook/fulltext/2026/"
    r, ms, err = _get(session, bulk)
    if r is None:
        attempts.append(f"bulk {bulk} -> {err}")
    else:
        attempts.append(f"bulk -> HTTP {r.status_code} ({len(r.content)} bytes, {ms}ms)")
        p.status, p.latency_ms, p.url = r.status_code, ms, bulk
        if r.status_code == 200:
            files = re.findall(r'href="(ipg\d{6}\.zip)"', r.text)
            if files:
                p.ok = True
                p.payload["latest_bulk_files"] = files[-3:]
                p.payload["bulk_file_count"] = len(files)
                # Size of the newest file, via a HEAD.
                try:
                    h = session.head(bulk + files[-1], timeout=TIMEOUT_S, allow_redirects=True)
                    size = int(h.headers.get("content-length", 0))
                    p.payload["latest_file_mb"] = round(size / 1_048_576, 1)
                except Exception:  # noqa: BLE001
                    pass
            else:
                attempts.append("bulk listing had no ipgYYMMDD.zip links")
    p.notes = attempts
    if not p.ok:
        p.error = "no keyless per-patent source confirmed"
    return p


PROBES: list[tuple[str, Callable]] = [
    ("fsa", probe_fsa),
    ("craigslist", probe_craigslist),
    ("globe", probe_globe),
    ("okstate", probe_okstate),
    ("patent", probe_patent),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "*/*"})

    results = {}
    for key, fn in PROBES:
        try:
            results[key] = fn(session, rng)
        except Exception as exc:  # noqa: BLE001
            results[key] = Probe(name=key, error=f"probe crashed: {type(exc).__name__}: {exc}")

    if args.json:
        print(json.dumps({k: asdict(v) for k, v in results.items()}, indent=2, default=str))
        return 0

    now = datetime.now(timezone.utc)
    print(f"\nDaily-input probe — {now.isoformat(timespec='seconds')}")
    print(f"Runner day-of-week: {now.strftime('%A')}\n")
    for key, p in results.items():
        mark = "PASS" if p.ok else "FAIL"
        print(f"[{mark}] {p.name}")
        if p.status is not None:
            print(f"       HTTP {p.status} in {p.latency_ms}ms — {p.url}")
        if p.error:
            print(f"       error: {p.error}")
        if p.payload:
            for line in json.dumps(p.payload, indent=2, default=str).splitlines():
                print(f"       {line}")
        for n in p.notes:
            print(f"       · {n}")
        print()

    passed = sum(1 for p in results.values() if p.ok)
    print(f"{passed}/{len(results)} sources fetchable without an API key.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
