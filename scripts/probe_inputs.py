"""Probe the candidate daily-input sources and report what Georgia would actually see.

This is a diagnostic, not part of the daily pipeline. It answers one question:
for each candidate input, can we fetch it without an API key, and what does
today's payload look like?

Every probe is individually defensive — a source that 403s, times out, or
changes shape reports a failure and the rest keep going. That mirrors how the
real fetchers would have to behave: a dead input is content, not an outage.

Where a source has no sanctioned feed, the probe checks robots.txt first and
reports what it says. We are not building a scraper that fights a site.

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
from urllib.parse import urlparse

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


def _robots_summary(session: requests.Session, url: str) -> str:
    """Fetch robots.txt for a URL's host and summarise the wildcard agent's rules."""
    host = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    r, _, err = _get(session, f"{host}/robots.txt")
    if r is None:
        return f"robots.txt unreachable ({err})"
    if r.status_code != 200:
        return f"robots.txt HTTP {r.status_code}"
    lines, in_star = [], False
    for line in r.text.splitlines():
        s = line.strip()
        if s.lower().startswith("user-agent:"):
            in_star = s.split(":", 1)[1].strip() == "*"
        elif in_star and s.lower().startswith(("disallow:", "allow:", "crawl-delay:")):
            lines.append(s)
    return f"robots.txt (User-agent: *): {'; '.join(lines[:12]) or 'no rules'}"


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
        p.error = f"not JSON ({exc}) — loc.gov serves CAPTCHA HTML with a 200 under load"
        return p

    results = data.get("results") or []
    if not results:
        p.error = f"no results at sp={page} — deep paging may be capped"
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
    }
    # The Oklahoma question: does this record carry a usable state signal?
    blob = json.dumps(item).lower()
    p.payload["mentions_oklahoma"] = "oklahoma" in blob
    p.notes.append(f"collection total: {data.get('pagination', {}).get('of', 'unknown')}")
    p.notes.append("no API key; documented limit 20 req/min, 1-hour ban if exceeded")
    return p


# --------------------------------------------------------------------------
# 2. Municipal surplus auctions (replaces Craigslist free stuff)
# --------------------------------------------------------------------------
SURPLUS_CANDIDATES = [
    "https://municibid.com/Browse/R138/Massachusetts",
    "https://municibid.com/",
    "https://www.govdeals.com/index.cfm?fa=Main.AdvSearchResultsNew&searchPg=Advanced&kWord=&stateID=MA",
]


def probe_surplus(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Municipal surplus auctions (Municibid / GovDeals)")
    p.notes.append(_robots_summary(session, "https://municibid.com/"))
    attempts = []
    for url in SURPLUS_CANDIDATES:
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
        # Surplus listings are overwhelmingly vehicles and equipment with a year.
        lots = re.findall(r"\b(?:19|20)\d{2}\s+[A-Z][A-Za-z\-]+(?:\s+[A-Z][A-Za-z\-]+){0,3}", r.text)
        bids = re.findall(r"\$[\d,]+(?:\.\d{2})?", text)
        if lots:
            p.ok = True
            p.payload = {"lot_shaped_strings": lots[:8], "price_strings": bids[:8]}
            p.notes.extend(attempts)
            return p
        attempts[-1] += " [200 but no lot-shaped text — likely JS-rendered]"
    p.notes.extend(attempts)
    p.error = "no listing source returned parseable lots"
    return p


# --------------------------------------------------------------------------
# 3. NYT corrections (replaces Boston Globe corrections)
# --------------------------------------------------------------------------
NYT_CANDIDATES = [
    "https://rss.nytimes.com/services/xml/rss/nyt/Corrections.xml",
    "https://www.nytimes.com/services/xml/rss/nyt/Corrections.xml",
    "https://www.nytimes.com/section/corrections",
]


def probe_nyt_corrections(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="NYT corrections")
    attempts = []
    for url in NYT_CANDIDATES:
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
        if r.status_code != 200 or reason:
            continue
        items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
        if items:
            parsed = []
            for raw in items[:5]:
                title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", raw, re.S)
                desc = re.search(
                    r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", raw, re.S
                )
                pub = re.search(r"<pubDate>(.*?)</pubDate>", raw, re.S)
                parsed.append({
                    "title": title.group(1).strip() if title else None,
                    "pubDate": pub.group(1).strip() if pub else None,
                    "description": re.sub(r"\s+", " ", desc.group(1)).strip()[:400] if desc else None,
                })
            p.ok = True
            p.payload = {"item_count": len(items), "items": parsed}
            p.notes = attempts
            return p
        attempts[-1] += " [200 but no <item> elements — not an RSS feed]"
    p.notes = attempts
    p.error = "no reachable corrections feed"
    return p


# --------------------------------------------------------------------------
# 4. Oklahoma State hockey — ACHA Men's Division 2
#
# OSU has no ACHA D1 program. They play ACHA MD2 (West), joined the ACHA in
# 2021, and reached the 2026 MD2 national championship game.
# --------------------------------------------------------------------------
OKST_CANDIDATES = [
    "https://www.okstatehockey.com/schedule",
    "https://m.okstatehockey.com/schedule/",
    "https://www.okstatehockey.com/schedule/upcoming",
    "https://www.eliteprospects.com/team/35165/oklahoma-state-univ",
    "https://www.achahockey.org/teams/oklahoma-state-university",
]


def probe_okstate(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Oklahoma State hockey (ACHA Men's D2)")
    today = datetime.now(timezone.utc).date()
    # ACHA MD2 runs roughly late September through the March nationals.
    in_season = today.month >= 9 or today.month <= 3
    p.payload["in_season"] = in_season
    if not in_season:
        p.notes.append(f"{today.isoformat()} is off-season — expect no results either way")
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
        scores = re.findall(r"\b\d{1,2}\s*[-–]\s*\d{1,2}\b", text)
        dates = re.findall(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b", text
        )
        if scores or dates:
            p.ok = True
            p.payload.update({
                "score_shaped_strings": scores[:8],
                "date_shaped_strings": dates[:8],
                "text_chars": len(text),
            })
            p.notes.extend(attempts)
            return p
        attempts[-1] += " [200 but no schedule text — likely JS-rendered]"
    p.notes.extend(attempts)
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
    today = datetime.now(timezone.utc).date()
    tuesday = _last_grant_tuesday(today)
    p.payload["most_recent_issue_day"] = tuesday.isoformat()
    p.payload["days_stale"] = (today - tuesday).days
    attempts = []

    # a) Open Data Portal — expected to demand an API key.
    odp = "https://api.uspto.gov/api/v1/patent/applications/search?q=*&limit=1"
    r, ms, err = _get(session, odp)
    if r is None:
        attempts.append(f"ODP -> {err}")
    else:
        attempts.append(f"ODP -> HTTP {r.status_code} ({ms}ms) {r.text[:160]!r}")

    # b) Bulk weekly grant XML — no key, but very large files.
    bulk = "https://bulkdata.uspto.gov/data/patent/grant/redbook/fulltext/2026/"
    r, ms, err = _get(session, bulk)
    if r is None:
        attempts.append(f"bulk -> {err}")
    else:
        attempts.append(f"bulk {bulk} -> HTTP {r.status_code} ({len(r.content)} bytes, {ms}ms)")
        p.status, p.latency_ms, p.url = r.status_code, ms, bulk
        if r.status_code == 200:
            files = re.findall(r'href="(ipg\d{6}\.zip)"', r.text)
            if files:
                p.ok = True
                p.payload["latest_bulk_files"] = files[-3:]
                p.payload["bulk_file_count"] = len(files)
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
    ("surplus", probe_surplus),
    ("nyt_corrections", probe_nyt_corrections),
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
    print(f"\nDaily-input probe — {now.isoformat(timespec='seconds')} ({now.strftime('%A')})\n")
    for key, p in results.items():
        print(f"[{'PASS' if p.ok else 'FAIL'}] {p.name}")
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
    print(f"{passed}/{len(results)} sources fetchable without an API key.")
    print("Note: run from a home IP and a PASS may not hold from a datacenter IP at 3am.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
