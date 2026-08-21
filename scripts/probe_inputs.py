"""Probe the candidate daily-input sources and report what Georgia would actually see.

Diagnostic only — not part of the daily pipeline.

Round 3. Round 2 scored 3/5, but two of those passes were hollow: the NYT
"pass" only proved an embedded JSON blob exists, and the hockey "pass" matched
EliteProspects' site-wide navigation rather than any Oklahoma State game. This
round tests the things that would actually break at 3am:

  - FSA: is the image full-size, and is the metadata complete enough to use?
  - Municibid: what IS the listing URL shape, since /Listing/Details/ isn't it?
  - Corrections: no NYT feed exists. Does the Guardian's open API carry the
    corrections column instead?
  - Hockey: both known sources are SPAs. Is there any server-rendered route?
  - Patent: ODP bulk pages are SPAs too. Is any file listing reachable?

    python3 scripts/probe_inputs.py            # human report
    python3 scripts/probe_inputs.py --only fsa # iterate on one
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests

TIMEOUT_S = 90
UA = "clarkle.com daily-input probe (jeff@clarkle.com)"
SAMPLE_CHARS = 700


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
    except Exception as exc:  # noqa: BLE001
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
    if r is None or r.status_code != 200:
        return f"robots.txt {err or f'HTTP {r.status_code}'}"
    lines, in_star = [], False
    for line in r.text.splitlines():
        s = line.strip()
        if s.lower().startswith("user-agent:"):
            in_star = s.split(":", 1)[1].strip() == "*"
        elif in_star and s.lower().startswith(("disallow:", "allow:", "crawl-delay:")):
            lines.append(s)
    return f"robots.txt (*): {'; '.join(lines[:12]) or 'no rules'}"


# --------------------------------------------------------------------------
# 1. FSA photograph — quality, not just reachability
#
# Round 2 returned "Boy Scouts, New York City": location null, subject [], and
# a 150px thumbnail from the /cph/ path rather than /fsa/. Reachable is not the
# same as usable. Measure how many records in a page are actually good.
# --------------------------------------------------------------------------
FSA_COLLECTION = "https://www.loc.gov/collections/fsa-owi-black-and-white-negatives/"


def _biggest_image(image_urls: list[str]) -> tuple[str | None, int]:
    """LoC appends #h=&w= to each variant. Pick the widest, not the last."""
    best, best_w = None, 0
    for u in image_urls or []:
        m = re.search(r"#h=(\d+)&w=(\d+)", u)
        w = int(m.group(2)) if m else 0
        if w >= best_w:
            best, best_w = u, w
    return best, best_w


def _usable(item: dict) -> tuple[bool, list[str]]:
    """A record Georgia can actually write about."""
    why = []
    img, w = _biggest_image(item.get("image_url") or [])
    if w < 640:
        why.append(f"image only {w}px wide")
    if not item.get("location"):
        why.append("no location")
    if not item.get("date"):
        why.append("no date")
    if not (item.get("title") or "").strip():
        why.append("no title")
    return (not why), why


def probe_fsa(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="FSA photograph (Library of Congress)")
    sp = rng.randint(1, 500)
    p.url = f"{FSA_COLLECTION}?fo=json&c=25&sp={sp}&at=results,pagination"
    r, ms, err = _get(session, p.url)
    p.latency_ms = ms
    if r is None:
        p.error = err
        return p
    p.status = r.status_code
    if reason := _blocked_reason(r):
        p.error = reason
        return p
    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        p.error = f"not JSON ({exc})"
        return p

    results = data.get("results") or []
    good = [it for it in results if _usable(it)[0]]
    rejects = Counter()
    for it in results:
        ok, why = _usable(it)
        if not ok:
            rejects.update(why[:1])

    p.payload["page_size"] = len(results)
    p.payload["usable_records"] = len(good)
    p.payload["rejection_reasons"] = dict(rejects)
    p.payload["collection_total"] = data.get("pagination", {}).get("of")

    if good:
        item = rng.choice(good)
        img, w = _biggest_image(item.get("image_url"))
        p.ok = True
        p.payload["pick"] = {
            "title": (item.get("title") or "")[:200],
            "date": item.get("date"),
            "location": item.get("location"),
            "id": item.get("id"),
            "image": img,
            "image_width": w,
            "collection_path": img.split("/pnp/")[1].split("/")[0] if img and "/pnp/" in img else None,
        }
    else:
        p.error = "no usable records on this page"

    # How often does Oklahoma actually come up?
    ok_url = f"{FSA_COLLECTION}?q=oklahoma&fo=json&c=1&at=pagination"
    rk, _, _ = _get(session, ok_url, timeout=60)
    if rk is not None and rk.status_code == 200:
        try:
            n = rk.json().get("pagination", {}).get("of")
            total = p.payload.get("collection_total") or 0
            p.payload["oklahoma_matches"] = n
            if n and total:
                p.payload["oklahoma_rate"] = f"1 in {round(total / n)} (~{n / total * 100:.2f}%)"
                p.payload["oklahoma_days_between_hits"] = round(total / n)
        except Exception:  # noqa: BLE001
            pass
    return p


# --------------------------------------------------------------------------
# 2. Municibid — find the actual listing URL shape
#
# Round 2: 240,995 bytes, zero /Listing/Details/ anchors. The content is
# server-rendered (the byte count is stable and large), so the selector is
# wrong. Enumerate every href shape on the page and let the data say.
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
    if reason := _blocked_reason(r):
        p.error = reason
        return p

    html = r.text
    hrefs = re.findall(r'href="(/[^"#?]{2,120})"', html)
    shapes = Counter("/" + "/".join(h.strip("/").split("/")[:2]) for h in hrefs)
    p.payload["total_hrefs"] = len(hrefs)
    p.payload["href_shapes"] = dict(shapes.most_common(12))

    # Is the catalogue shipped as JSON inside the page instead?
    blobs = re.findall(r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.*?)</script>', html, re.S)
    p.payload["json_script_blocks"] = len(blobs)
    if blobs:
        p.payload["json_block_sample"] = blobs[0][:SAMPLE_CHARS]

    # Money and years are what listings are made of — where do they live?
    money = re.findall(r"\$[\d,]+(?:\.\d{2})?", html)
    p.payload["dollar_amounts_found"] = len(money)
    p.payload["dollar_sample"] = money[:8]

    # Grab the markup immediately around the first price as a parser target.
    m = re.search(r".{400}\$[\d,]+(?:\.\d{2})?.{300}", html, re.S)
    if m:
        p.payload["around_first_price"] = re.sub(r"\s+", " ", m.group(0))[:SAMPLE_CHARS]

    p.ok = bool(money) and any(k.startswith("/Listing") for k in shapes)
    if not p.ok:
        p.error = "listings not found in server HTML — check href_shapes and around_first_price"
    return p


# --------------------------------------------------------------------------
# 3. Corrections — the NYT has no feed; try the Guardian's open API
#
# Round 2 settled it: 74 feeds advertised at nytimes.com/rss, none for
# corrections, and the section page ships 872KB with only 3.4KB of visible
# text — the list lives in __preloadedData. Scraping a paywalled publisher's
# SPA state is not a foundation for a daily cron.
#
# The Guardian runs an open, documented API and publishes a standing
# "Corrections and clarifications" column. api-key=test is their public
# developer tier.
# --------------------------------------------------------------------------
GUARDIAN_SERIES = (
    "https://content.guardianapis.com/theguardian/series/corrections-and-clarifications"
    "?api-key=test&show-fields=headline,bodyText,firstPublicationDate&page-size=5&order-by=newest"
)
NYT_SECTION = "https://www.nytimes.com/section/corrections"


def probe_corrections(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Corrections column (Guardian open API)")
    r, ms, err = _get(session, GUARDIAN_SERIES)
    p.url, p.latency_ms = GUARDIAN_SERIES.split("?")[0], ms
    if r is None:
        p.error = err
    else:
        p.status = r.status_code
        try:
            body = r.json().get("response", {})
            results = body.get("results", []) or []
            p.payload["guardian_status"] = body.get("status")
            p.payload["total_available"] = body.get("total")
            p.payload["items"] = [
                {
                    "date": (it.get("fields", {}) or {}).get("firstPublicationDate")
                            or it.get("webPublicationDate"),
                    "headline": (it.get("fields", {}) or {}).get("headline") or it.get("webTitle"),
                    "excerpt": re.sub(
                        r"\s+", " ", ((it.get("fields", {}) or {}).get("bodyText") or "")
                    )[:300],
                }
                for it in results[:3]
            ]
            p.ok = bool(results)
            if not p.ok:
                p.error = f"API answered but returned no items: {json.dumps(body)[:300]}"
        except Exception as exc:  # noqa: BLE001
            p.error = f"unparseable response ({exc}): {r.text[:200]}"

    # Record what the NYT route would cost, for the record.
    nr, nms, nerr = _get(session, NYT_SECTION)
    if nr is not None and nr.status_code == 200:
        pre = "__preloadedData" in nr.text
        p.notes.append(
            f"NYT fallback: section page HTTP 200, {len(nr.content)} bytes, "
            f"{len(_text(nr.text))} chars visible text, __preloadedData={pre} "
            "— parseable only by mining SPA state"
        )
    return p


# --------------------------------------------------------------------------
# 4. Oklahoma State hockey — is ANY route server-rendered?
#
# Round 2: EliteProspects returned 184KB but only 4,193 chars of text, and the
# "linked teams" were Blackhawks / Red Wings / Rangers — site navigation, not
# OSU's opponents. Both known sources are SPAs. Last look: find the real ACHA
# URLs from their own homepage rather than guessing again.
# --------------------------------------------------------------------------
ACHA_HOME = "https://www.achahockey.org/"
EP_SEASON = "https://www.eliteprospects.com/team/35165/oklahoma-state-univ/2025-2026"


def probe_okstate(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Oklahoma State hockey (ACHA Men's D2)")
    today = datetime.now(timezone.utc).date()
    p.payload["in_season"] = today.month >= 9 or today.month <= 3

    # Discover real ACHA routes instead of guessing at /teams/<slug>.
    r, ms, err = _get(session, ACHA_HOME)
    if r is not None and r.status_code == 200:
        hrefs = re.findall(r'href="([^"#]+)"', r.text)
        interesting = sorted({
            h for h in hrefs
            if re.search(r"(team|schedule|standing|score|stats|roster)", h, re.I)
        })
        p.payload["acha_routes"] = interesting[:15]
        p.payload["acha_text_chars"] = len(_text(r.text))
        p.notes.append(f"{ACHA_HOME} -> HTTP 200, {len(r.content)} bytes")
    else:
        p.notes.append(f"{ACHA_HOME} -> {err or f'HTTP {r.status_code}'}")

    # EP season-scoped page: does it carry a real game table server-side?
    r2, ms2, err2 = _get(session, EP_SEASON)
    if r2 is not None:
        body = _text(r2.text)
        p.url, p.status, p.latency_ms = EP_SEASON, r2.status_code, ms2
        p.payload["ep_season_status"] = r2.status_code
        p.payload["ep_html_bytes"] = len(r2.content)
        p.payload["ep_text_chars"] = len(body)
        # A real game table names ACHA opponents, not NHL clubs.
        acha_words = re.findall(
            r"\b(?:Univ|University|State|College|Cowboys|ACHA)\b", body
        )
        p.payload["ep_college_words"] = len(acha_words)
        p.payload["ep_sample"] = body[:SAMPLE_CHARS]
        p.ok = len(body) > 20000 and len(acha_words) > 10
    if not p.ok:
        p.error = "no server-rendered game data found on any route"
    return p


# --------------------------------------------------------------------------
# 5. Patent granted (USPTO)
#
# Round 2: data.uspto.gov/bulkdata and .../datasets/PTGRXML both returned an
# identical 20,666 bytes with zero visible text — the same React shell. The
# file list comes from an XHR the shell makes. Find that endpoint and see
# whether it needs the key.
# --------------------------------------------------------------------------
ODP_PRODUCT_API = "https://api.uspto.gov/api/v1/datasets/products/PTGRXML"
ODP_SEARCH_API = "https://api.uspto.gov/api/v1/datasets/products/search?q=patent%20grant"
LEGACY_BULK = "https://bulkdata.uspto.gov/"


def _last_grant_tuesday(today: date) -> date:
    """US patents issue on Tuesdays. Return the most recent one."""
    return today - timedelta(days=(today.weekday() - 1) % 7)


def probe_patent(session: requests.Session, rng: random.Random) -> Probe:
    p = Probe(name="Patent granted (USPTO)")
    today = datetime.now(timezone.utc).date()
    tuesday = _last_grant_tuesday(today)
    p.payload["most_recent_issue_day"] = tuesday.isoformat()
    p.payload["days_stale"] = (today - tuesday).days

    for label, url in (
        ("product API", ODP_PRODUCT_API),
        ("product search", ODP_SEARCH_API),
        ("legacy host", LEGACY_BULK),
    ):
        r, ms, err = _get(session, url, timeout=45)
        if r is None:
            p.notes.append(f"{label} -> {err}")
            continue
        snippet = re.sub(r"\s+", " ", r.text)[:200]
        p.notes.append(f"{label} -> HTTP {r.status_code} ({len(r.content)}B, {ms}ms) {snippet!r}")
        if r.status_code == 200:
            try:
                data = r.json()
                files = json.dumps(data)
                if "ipg" in files or "fileDownloadUri" in files:
                    p.ok = True
                    p.url, p.status, p.latency_ms = url, r.status_code, ms
                    p.payload["response_keys"] = list(data)[:12]
                    p.payload["sample"] = files[:SAMPLE_CHARS]
                    return p
            except Exception:  # noqa: BLE001
                pass
    p.error = "no keyless file listing — an ODP API key (USPTO.gov account + MFA) is required"
    return p


PROBES: list[tuple[str, Callable]] = [
    ("fsa", probe_fsa),
    ("surplus", probe_surplus),
    ("corrections", probe_corrections),
    ("okstate", probe_okstate),
    ("patent", probe_patent),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", help="comma-separated probe keys")
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
    print(f"\nDaily-input probe r3 — {now.isoformat(timespec='seconds')} ({now.strftime('%A')})\n")
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
    print(f"{passed}/{len(results)} sources usable as-is.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
