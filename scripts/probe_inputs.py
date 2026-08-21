"""Probe the candidate daily-input sources and report what Georgia would actually see.

This is a diagnostic, not part of the daily pipeline. Round 2: the first run
established which hosts answer at all. This one asks the harder question —
what is actually *in* the response, and can we parse it without a browser.

Every probe is individually defensive. Where a source has no sanctioned feed,
the probe checks robots.txt first. We are not building a scraper that fights
a site.

Successful probes dump a `sample` excerpt so the real parser can be written
against evidence rather than guesses.

    python3 scripts/probe_inputs.py            # human report
    python3 scripts/probe_inputs.py --json     # machine-readable
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
from urllib.parse import urljoin, urlparse

import requests

TIMEOUT_S = 90  # loc.gov deep queries are genuinely slow; 30s was too tight
UA = "clarkle.com daily-input probe (jeff@clarkle.com)"
SAMPLE_CHARS = 600


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
    start = time.time()
    try:
        r = session.get(url, timeout=kw.pop("timeout", TIMEOUT_S), **kw)
        return r, int((time.time() - start) * 1000), ""
    except Exception as exc:  # noqa: BLE001 — a probe must never raise
        return None, int((time.time() - start) * 1000), f"{type(exc).__name__}: {exc}"


def _blocked_reason(r: requests.Response) -> str:
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


def _text(html: str) -> str:
    t = re.sub(r"<(script|style|noscript).*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _robots_summary(session: requests.Session, url: str) -> str:
    host = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    r, _, err = _get(session, f"{host}/robots.txt", timeout=20)
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
# 1. FSA photograph — Library of Congress
#
# Round 1 died on a 30s read timeout at sp=<random up to 500>. Deep paging is
# the prime suspect, so time a shallow page against a deep one and find out
# whether random sampling by offset is viable at all.
# --------------------------------------------------------------------------
FSA_COLLECTION = "https://www.loc.gov/collections/fsa-owi-black-and-white-negatives/"


def probe_fsa(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="FSA photograph (Library of Congress)")
    timings = {}

    for label, sp in (("shallow sp=1", 1), ("deep sp=%d" % rng.randint(200, 500), None)):
        sp = sp or int(label.split("=")[1])
        url = f"{FSA_COLLECTION}?fo=json&c=25&sp={sp}&at=results,pagination"
        r, ms, err = _get(session, url)
        timings[label] = f"{ms}ms " + (err or f"HTTP {r.status_code}")
        if r is None or r.status_code != 200:
            continue
        reason = _blocked_reason(r)
        if reason:
            timings[label] += f" [{reason}]"
            continue
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            timings[label] += " [200 but not JSON — CAPTCHA HTML]"
            continue
        results = data.get("results") or []
        if not results:
            timings[label] += " [no results — deep paging capped?]"
            continue
        if not p.ok:
            item = rng.choice(results)
            imgs = item.get("image_url") or []
            p.ok = True
            p.url, p.status, p.latency_ms = url, r.status_code, ms
            p.payload = {
                "title": item.get("title"),
                "date": item.get("date"),
                "location": item.get("location"),
                "subject": (item.get("subject") or [])[:6],
                "id": item.get("id"),
                "image_url": imgs[-1] if imgs else None,
                "mentions_oklahoma": "oklahoma" in json.dumps(item).lower(),
                "collection_total": data.get("pagination", {}).get("of"),
            }
    p.notes.append("timings: " + json.dumps(timings))
    p.notes.append("no API key; documented limit 20 req/min, 1-hour ban if exceeded")
    if not p.ok:
        p.error = "no page returned parseable results"
    return p


# --------------------------------------------------------------------------
# 2. Municipal surplus auctions
#
# Round 1: HTTP 200, 240KB, robots.txt permits /Browse/. The content is there;
# my regex was garbage (it matched the copyright footer). Dump structured
# candidates and a raw sample so a real parser can be written.
# --------------------------------------------------------------------------
SURPLUS_URL = "https://municibid.com/Browse/R138/Massachusetts"


def probe_surplus(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Municipal surplus auctions (Municibid MA)")
    p.notes.append(_robots_summary(session, SURPLUS_URL))
    r, ms, err = _get(session, SURPLUS_URL)
    p.latency_ms, p.url = ms, SURPLUS_URL
    if r is None:
        p.error = err
        return p
    p.status = r.status_code
    reason = _blocked_reason(r)
    if reason:
        p.error = reason
        return p

    html = r.text
    # Listing links are the reliable anchor on this site.
    links = re.findall(r'href="(/Listing/Details/[^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I)
    titles = [re.sub(r"\s+", " ", _text(t)).strip() for _, t in links]
    titles = [t for t in titles if len(t) > 3]
    bids = re.findall(r"(?:Current Bid|Bid|Price)[^$]{0,40}(\$[\d,]+(?:\.\d{2})?)", html, re.I)
    # Sellers are what make this input interesting — which town is selling.
    sellers = re.findall(r"(?:Town|City|Borough|Township|Village) of ([A-Z][A-Za-z\s\-]{2,30})", html)

    p.payload = {
        "listing_links": len(links),
        "sample_titles": titles[:6],
        "sample_bids": bids[:6],
        "sample_sellers": sorted(set(s.strip() for s in sellers))[:8],
        "html_bytes": len(r.content),
    }
    p.ok = bool(titles)
    if not p.ok:
        p.error = "no /Listing/Details/ anchors found — check the sample"
        m = re.search(r"(?i)(<div[^>]*(?:listing|item|card|lot)[^>]*>.{0,%d})" % SAMPLE_CHARS, html, re.S)
        p.payload["sample"] = (m.group(1) if m else html[:SAMPLE_CHARS])[:SAMPLE_CHARS]
    return p


# --------------------------------------------------------------------------
# 3. NYT corrections
#
# Round 1: both guessed RSS paths 404'd, but the section page returned 200 and
# 872KB with no Cloudflare wall. So: discover the real feed list instead of
# guessing, and check the section page for the embedded JSON payload NYT ships
# inside its HTML.
# --------------------------------------------------------------------------
NYT_RSS_INDEX = "https://www.nytimes.com/rss"
NYT_SECTION = "https://www.nytimes.com/section/corrections"


def probe_nyt_corrections(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="NYT corrections")

    # a) What feeds actually exist?
    r, ms, err = _get(session, NYT_RSS_INDEX)
    if r is not None and r.status_code == 200:
        feeds = sorted(set(re.findall(r'https?://[^\s"\'<>]+?\.xml', r.text)))
        p.payload["feeds_advertised"] = len(feeds)
        p.payload["corrections_feed"] = [f for f in feeds if "correction" in f.lower()] or None
        p.payload["feed_sample"] = feeds[:8]
        p.notes.append(f"{NYT_RSS_INDEX} -> HTTP 200, {len(feeds)} .xml URLs advertised")
    else:
        p.notes.append(f"{NYT_RSS_INDEX} -> {err or f'HTTP {r.status_code}'}")

    # If a real corrections feed exists, use it.
    for feed in (p.payload.get("corrections_feed") or []):
        fr, fms, ferr = _get(session, feed)
        if fr is not None and fr.status_code == 200 and "<item>" in fr.text:
            items = re.findall(r"<item>(.*?)</item>", fr.text, re.S)[:5]
            p.ok = True
            p.url, p.status, p.latency_ms = feed, fr.status_code, fms
            p.payload["items"] = [
                {
                    "title": (re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", i, re.S)
                              or [None, None])[1],
                    "pubDate": (re.search(r"<pubDate>(.*?)</pubDate>", i, re.S) or [None, None])[1],
                }
                for i in items
            ]
            return p

    # b) No feed — is the section page parseable server-side?
    r, ms, err = _get(session, NYT_SECTION)
    if r is None:
        p.error = err
        return p
    p.url, p.status, p.latency_ms = NYT_SECTION, r.status_code, ms
    reason = _blocked_reason(r)
    if reason:
        p.error = reason
        return p

    html = r.text
    has_preload = "__preloadedData" in html
    p.payload["has_embedded_json"] = has_preload
    body = _text(html)
    # Corrections read "An article on Tuesday about X misstated ..."
    hits = re.findall(
        r"((?:An article|A picture caption|An obituary|A review|Because of an editing error)"
        r"[^.]{10,300}\.)",
        body,
    )
    p.payload["correction_shaped_sentences"] = hits[:4]
    p.payload["text_chars"] = len(body)
    p.ok = bool(hits) or has_preload
    if not p.ok:
        p.payload["sample"] = body[:SAMPLE_CHARS]
        p.error = "section page reachable but nothing corrections-shaped in the HTML"
    return p


# --------------------------------------------------------------------------
# 4. Oklahoma State hockey — ACHA Men's D2
#
# Round 1: okstatehockey.com returned an identical 11,493-byte shell for three
# different routes — it's a JS SPA, dead without a headless browser. Drop it.
# EliteProspects returned 184KB of real HTML; my date regex just didn't match
# their format. Widen the patterns and dump a sample.
# --------------------------------------------------------------------------
EP_URL = "https://www.eliteprospects.com/team/35165/oklahoma-state-univ"
ACHA_SEARCH = "https://www.achahockey.org/"

DATE_PATTERNS = [
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b",
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b",
]


def probe_okstate(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Oklahoma State hockey (ACHA Men's D2)")
    today = datetime.now(timezone.utc).date()
    p.payload["in_season"] = today.month >= 9 or today.month <= 3
    if not p.payload["in_season"]:
        p.notes.append(f"{today.isoformat()} is off-season — an empty result is expected")

    r, ms, err = _get(session, EP_URL)
    p.url, p.latency_ms = EP_URL, ms
    if r is None:
        p.error = err
        return p
    p.status = r.status_code
    reason = _blocked_reason(r)
    if reason:
        p.error = reason
        return p

    html = r.text
    body = _text(html)
    dates = []
    for pat in DATE_PATTERNS:
        dates.extend(re.findall(pat, body))
    scores = re.findall(r"\b\d{1,2}\s*[-–:]\s*\d{1,2}\b", body)
    # EP tables carry opponent links; those tell us whether games are listed.
    opponents = re.findall(r'/team/\d+/([a-z0-9\-]+)"', html)
    p.payload.update({
        "date_strings": dates[:8],
        "score_strings": scores[:8],
        "linked_teams": sorted(set(opponents))[:10],
        "text_chars": len(body),
    })
    p.ok = bool(dates or scores)
    if not p.ok:
        p.payload["sample"] = body[:SAMPLE_CHARS]
        p.error = "200 but nothing date- or score-shaped; see sample"
    p.notes.append("okstatehockey.com dropped — confirmed 11,493-byte SPA shell on all routes")
    return p


# --------------------------------------------------------------------------
# 5. Patent granted (USPTO)
#
# Round 1: ODP returned a clean 401 (key required, as documented) and
# bulkdata.uspto.gov failed DNS resolution. Bulk products now live under the
# Open Data Portal at data.uspto.gov/bulkdata.
# --------------------------------------------------------------------------
PTGRXML = "https://data.uspto.gov/bulkdata/datasets/PTGRXML"
BULK_INDEX = "https://data.uspto.gov/bulkdata"


def _last_grant_tuesday(today: date) -> date:
    """US patents issue on Tuesdays. Return the most recent one."""
    return today - timedelta(days=(today.weekday() - 1) % 7)


def probe_patent(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Patent granted (USPTO)")
    today = datetime.now(timezone.utc).date()
    tuesday = _last_grant_tuesday(today)
    p.payload["most_recent_issue_day"] = tuesday.isoformat()
    p.payload["days_stale"] = (today - tuesday).days

    for label, url in (("bulk index", BULK_INDEX), ("PTGRXML dataset", PTGRXML)):
        r, ms, err = _get(session, url)
        if r is None:
            p.notes.append(f"{label} {url} -> {err}")
            continue
        reason = _blocked_reason(r)
        p.notes.append(
            f"{label} {url} -> HTTP {r.status_code} ({len(r.content)} bytes, {ms}ms)"
            + (f" [{reason}]" if reason else "")
        )
        if r.status_code != 200 or reason:
            continue
        p.url, p.status, p.latency_ms = url, r.status_code, ms
        zips = re.findall(r'href="([^"]*ipg\d{6}[^"]*\.zip)"', r.text)
        if zips:
            p.ok = True
            p.payload["latest_files"] = zips[-3:]
            try:
                h = session.head(urljoin(url, zips[-1]), timeout=30, allow_redirects=True)
                p.payload["latest_file_mb"] = round(
                    int(h.headers.get("content-length", 0)) / 1_048_576, 1
                )
                p.payload["head_status"] = h.status_code
            except Exception:  # noqa: BLE001
                pass
            return p
        # The ODP pages are React apps; note whether the file list is embedded.
        p.payload.setdefault("sample", _text(r.text)[:SAMPLE_CHARS])
    p.error = "no keyless per-patent source confirmed"
    p.notes.append("ODP API returned a clean 401 in round 1 — key + MFA account required")
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
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", help="comma-separated probe keys to run")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)

    wanted = set(args.only.split(",")) if args.only else None
    rng = random.Random(args.seed)
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "*/*"})

    results = {}
    for key, fn in PROBES:
        if wanted and key not in wanted:
            continue
        try:
            results[key] = fn(session, rng)
        except Exception as exc:  # noqa: BLE001
            results[key] = Probe(name=key, error=f"probe crashed: {type(exc).__name__}: {exc}")

    if args.json:
        print(json.dumps({k: asdict(v) for k, v in results.items()}, indent=2, default=str))
        return 0

    now = datetime.now(timezone.utc)
    print(f"\nDaily-input probe r2 — {now.isoformat(timespec='seconds')} ({now.strftime('%A')})\n")
    for p in results.values():
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
    print(f"{passed}/{len(results)} sources parseable without a headless browser.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
