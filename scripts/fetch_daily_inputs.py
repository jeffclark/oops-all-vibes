"""Fetch Georgia's daily inputs and write the Layer 5 JSON.

Five narrow, recurring sources. The point is not novelty — it's accumulation.
A hundred days of one town's weather is a novel; a hundred days of "the news"
is a hundred days of nothing. So each source is deliberately small, always the
same, and carried forward with its own history.

Resilient by design, exactly like fetch_feedback: every source is fetched
independently, any failure is recorded as a failure and the rest continue, and
nothing here can crash the daily pipeline. A source that goes dark is content,
not an outage — Georgia is told what failed and left to make of it what she
wants.

    python -m scripts.fetch_daily_inputs           # writes inputs/<today>.json
    python -m scripts.fetch_daily_inputs --dry-run # print, write nothing
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import random
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUTS_DIR = REPO_ROOT / "inputs"
ROSTER_FILE = INPUTS_DIR / "roster.json"

REQUEST_TIMEOUT_S = 45
UA = "clarkle.com daily-input fetcher (jeff@clarkle.com)"
# Municibid 403s a bare bot UA but serves anyone who looks like a browser.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

# How many builds Georgia gets with an input before she must retire one.
RETIRE_EVERY_BUILDS = 30


def _warn(msg: str) -> None:
    print(f"fetch_daily_inputs: {msg}", file=sys.stderr)


# ==========================================================================
# 1. FSA/OWI photograph — Library of Congress
# ==========================================================================
FSA_COLLECTION = "https://www.loc.gov/collections/fsa-owi-black-and-white-negatives/"
FSA_MIN_IMAGE_WIDTH = 640
# Every record is tagged "united states"; on its own it locates nothing.
FSA_GENERIC_PLACES = {"united states", "usa", "america"}
FSA_SAMPLE_PAGES = 500


def _biggest_image(image_urls: list[str] | None) -> tuple[str | None, int]:
    """LoC appends #h=&w= to each variant. Pick the widest — not the last."""
    best, best_w = None, 0
    for u in image_urls or []:
        m = re.search(r"#h=(\d+)&w=(\d+)", u)
        w = int(m.group(2)) if m else 0
        if w >= best_w:
            best, best_w = u, w
    return best, best_w


def _fsa_usable(item: dict) -> bool:
    """A record with enough substance for Georgia to say something about."""
    _, w = _biggest_image(item.get("image_url"))
    places = [str(x).strip().lower() for x in (item.get("location") or [])]
    return bool(
        w >= FSA_MIN_IMAGE_WIDTH
        and [x for x in places if x not in FSA_GENERIC_PLACES]
        and item.get("date")
        and (item.get("title") or "").strip()
    )


def fetch_fsa(session: requests.Session, rng: random.Random) -> dict:
    """One photograph from the 171,074 FSA/OWI negatives, 1935-1944.

    loc.gov drops connections mid-response often enough to matter, and a page
    can come back with nothing usable on it, so try a few different pages
    before calling the source dead.
    """
    data, usable, last_err = None, [], None
    for _ in range(4):
        page = rng.randint(1, FSA_SAMPLE_PAGES)
        try:
            url = f"{FSA_COLLECTION}?fo=json&c=25&sp={page}&at=results,pagination"
            r = session.get(url, timeout=REQUEST_TIMEOUT_S, headers={"User-Agent": UA})
            r.raise_for_status()
            # Under load loc.gov serves CAPTCHA HTML with a 200, so JSON is the real check.
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            last_err = f"page {page}: {type(exc).__name__}"
            continue
        usable = [it for it in (data.get("results") or []) if _fsa_usable(it)]
        if usable:
            break
        last_err = f"page {page}: no usable records"
    if not usable:
        raise ValueError(last_err or "no usable records")

    item = rng.choice(usable)
    image, width = _biggest_image(item.get("image_url"))
    location = item.get("location") or []
    year = re.search(r"(19\d{2})", str(item.get("date") or ""))
    year = int(year.group(1)) if year else None

    return {
        "title": _redact_contacts(item.get("title") or ""),
        "date": item.get("date"),
        "location": location,
        "subject": (item.get("subject") or [])[:8],
        "image": image,
        "image_width": width,
        "item_url": item.get("id"),
        # The collection spans two very different projects. Which one she drew
        # changes the register completely, so name it rather than let her guess.
        "era": "FSA (Depression)" if year and year <= 1941 else "OWI (wartime)",
        "is_oklahoma": any("oklahoma" in str(x).lower() for x in location),
        "collection_total": (data.get("pagination") or {}).get("of"),
    }


# ==========================================================================
# 2. Boston 311 — what the city reported broken yesterday
# ==========================================================================
CKAN_BASE = "https://data.boston.gov/api/3/action"
CKAN_311_PACKAGE = "311-service-requests"
CKAN_311_FALLBACK_RESOURCE = "1a0b420d-99f1-4887-9851-990b2a5a6e17"  # 2026 CSV
CIVIC_MAX_LOOKBACK_DAYS = 5

# Case titles arrive with scheduling noise bolted on: "SCH 8/27 Bed Bugs",
# "DISP 8/19 Maintenance Complaint". Strip it so counts group correctly.
_CASE_NOISE = re.compile(r"^\s*(?:SCH|DISP|RESCH)?\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*", re.I)


# Closure notes open with one of two machine stamps before the human text:
#   "Case Closed. Closed date : Wed Aug 19 04:20:21 EDT 2026 Noted Abatement issued"
#   "Case Closed. Closed date : 2026-08-19 20:25:08.833 Case Resolved The violator..."
# Strip the stamp and the status word so what is left is what somebody typed.
_STAMP = re.compile(
    r"^\s*Case\s+Closed\.?\s*Closed\s+date\s*:\s*"
    r"(?:[A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+[\d:]+\s+[A-Z]{3}\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2}[\sT][\d:.]+)\s*",
    re.I,
)
_STATUS = re.compile(r"^\s*(?:Case\s+)?(?:Closed|Resolved|Noted|Invalid|Duplicate)\s*", re.I)
# Workers sometimes paste their own address or a constituent's phone number into
# the note. The note goes into a public prompt and onto a public page; the
# contact details are not the interesting part of it.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


def _redact_contacts(text: str) -> str:
    """Any fetched free text can carry a real person's address or number."""
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_closure_stamp(text: str) -> str:
    text = _STAMP.sub("", text)
    text = _STATUS.sub("", text)
    return _redact_contacts(text)


def _clean_case_title(title: str | None) -> str:
    return _CASE_NOISE.sub("", (title or "").strip()).strip() or "Unspecified"


def _ckan_sql(session: requests.Session, sql: str) -> list[dict]:
    r = session.get(
        f"{CKAN_BASE}/datastore_search_sql",
        params={"sql": sql},
        timeout=REQUEST_TIMEOUT_S,
        headers={"User-Agent": UA},
    )
    r.raise_for_status()
    body = r.json()
    if not body.get("success"):
        raise ValueError(f"CKAN error: {json.dumps(body)[:200]}")
    return body["result"]["records"]


def _resolve_311_resource(session: requests.Session, year: int) -> str:
    """Find this year's 311 CSV resource id; the dataset rolls over annually."""
    try:
        r = session.get(
            f"{CKAN_BASE}/package_show",
            params={"id": CKAN_311_PACKAGE},
            timeout=REQUEST_TIMEOUT_S,
            headers={"User-Agent": UA},
        )
        r.raise_for_status()
        for res in r.json()["result"]["resources"]:
            if res.get("format", "").upper() == "CSV" and str(year) in (res.get("name") or ""):
                return res["id"]
    except Exception as exc:  # noqa: BLE001
        _warn(f"311 resource lookup failed ({exc}); using pinned id")
    return CKAN_311_FALLBACK_RESOURCE


def fetch_civic(session: requests.Session, rng: random.Random, day: date) -> dict:
    """Yesterday's 311 calls: the shape of the day, plus what a worker wrote back.

    Street addresses are deliberately dropped. The closure notes are the point
    and the neighborhood is plenty of texture; pinning a needle pickup or an
    overcrowding complaint to somebody's front door is not.
    """
    rid = _resolve_311_resource(session, day.year)

    # The published extract lags the live queue by a day or two, so walk back to
    # the most recent day that actually has cases in it.
    tally: dict[str, int] = {}
    window = ""
    for back in range(CIVIC_MAX_LOOKBACK_DAYS):
        probe = day - timedelta(days=back)
        window = (f"open_dt >= '{probe.isoformat()}' "
                  f"AND open_dt < '{(probe + timedelta(days=1)).isoformat()}'")
        # No LIMIT: the cleaned titles are counted in Python, so a truncated
        # result would silently make total_cases the size of the slice rather
        # than the day, and drop the one-offs — which are the interesting rows.
        counts = _ckan_sql(
            session,
            f'SELECT case_title, COUNT(*) AS n FROM "{rid}" WHERE {window} '
            "GROUP BY case_title ORDER BY n DESC",
        )
        for row in counts:
            title = _clean_case_title(row["case_title"])
            tally[title] = tally.get(title, 0) + int(row["n"])
        if tally:
            day = probe
            break
    if not tally:
        raise ValueError(f"no 311 cases in the {CIVIC_MAX_LOOKBACK_DAYS} days to {day}")

    ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    notes = _ckan_sql(
        session,
        f"SELECT case_title, neighborhood, closure_reason FROM \"{rid}\" WHERE {window} "
        "AND closure_reason IS NOT NULL AND closure_reason != '' LIMIT 120",
    )
    rng.shuffle(notes)
    resolved = []
    for row in notes:
        text = re.sub(r"\s+", " ", (row.get("closure_reason") or "")).strip()
        text = _strip_closure_stamp(text)
        if len(text) < 8:
            continue
        resolved.append({
            "case": _clean_case_title(row.get("case_title")),
            "neighborhood": html_lib.unescape(row.get("neighborhood") or "unspecified"),
            "note": text[:300],
        })
        if len(resolved) == 3:
            break

    return {
        "day": day.isoformat(),
        "total_cases": sum(tally.values()),
        "distinct_types": len(tally),
        "top": [{"case": k, "n": v} for k, v in ranked[:6]],
        "only_one_of": [k for k, v in ranked if v == 1][:8],
        "resolved": resolved,
    }


# ==========================================================================
# 3. Municipal surplus auctions — Municibid
# ==========================================================================
MB_SEARCH = "https://municibid.com/Listing/Search/Results"
MB_PAGES = 12


def _ld_blocks(html: str) -> list[Any]:
    out = []
    for raw in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            # The listing block ships literal newlines inside strings.
            out.append(json.loads(raw.strip(), strict=False))
        except Exception:  # noqa: BLE001
            continue
    return out


def fetch_surplus(session: requests.Session, rng: random.Random) -> dict:
    """One thing a government is trying to get rid of."""
    headers = {"User-Agent": BROWSER_UA}
    page = rng.randint(1, MB_PAGES)
    r = session.get(
        MB_SEARCH, params={"Page": page}, timeout=REQUEST_TIMEOUT_S,
        headers=headers, allow_redirects=True,
    )
    r.raise_for_status()

    listings: list[tuple[str, str]] = []
    for block in _ld_blocks(r.text):
        if isinstance(block, dict) and block.get("@type") == "ItemList":
            for el in block.get("itemListElement") or []:
                if el.get("name") and el.get("url"):
                    listings.append((el["name"], el["url"]))
    if not listings:
        # The ItemList block only ships on some query shapes; the listing links
        # are always there. The slug carries the title well enough.
        for lid, slug in set(re.findall(r"/Listing/Details/(\d+)/([A-Za-z0-9\-]+)", r.text)):
            listings.append((slug.replace("-", " "),
                             f"https://municibid.com/Listing/Details/{lid}"))
    # Sellers really do post these, and they are not interesting.
    listings = [(n, u) for n, u in listings if "test item" not in n.lower()]
    if not listings:
        raise ValueError(f"no listings on page {page}")

    name, url = rng.choice(listings)
    detail = {"name": _redact_contacts(html_lib.unescape(name)), "url": url}
    try:
        dr = session.get(url, timeout=REQUEST_TIMEOUT_S, headers=headers, allow_redirects=True)
        dr.raise_for_status()
        for block in _ld_blocks(dr.text):
            if isinstance(block, dict) and block.get("@type") == "Product":
                offers = block.get("offers") or {}
                desc = _redact_contacts(
                    html_lib.unescape(re.sub(r"\s+", " ", block.get("description") or ""))
                )
                seller = (offers.get("seller") or {}).get("name")
                detail.update({
                    "description": desc[:600],
                    "seller": html_lib.unescape(seller) if seller else None,
                    "price": offers.get("price"),
                    "currency": offers.get("priceCurrency"),
                })
                break
    except Exception as exc:  # noqa: BLE001
        # The listing title alone is still worth having.
        _warn(f"surplus detail fetch failed ({exc}); keeping the title")
        detail["detail_error"] = str(exc)[:120]

    detail["listings_on_page"] = len(listings)
    return detail


# ==========================================================================
# 4. Oklahoma State hockey — ACHA Men's Division 2
#
# OSU has no ACHA D1 program: the league's own team list has them as
# "MD2 Oklahoma State University" (id 509), while Oklahoma and Central
# Oklahoma are MD1. The ACHA runs on HockeyTech, whose feed is open — the
# client key is published in the league's own statview bootstrap.
# ==========================================================================
HT_FEED = "https://lscluster.hockeytech.com/feed/index.php"
HT_KEY = "e6867b36742a0c9d"
HT_CLIENT = "acha"
OKSTATE_TEAM_ID = "509"


def _ht(session: requests.Session, view: str, **params) -> dict:
    r = session.get(
        HT_FEED,
        params={"key": HT_KEY, "client_code": HT_CLIENT, "fmt": "json",
                "feed": "modulekit", "view": view, **params},
        timeout=REQUEST_TIMEOUT_S,
        headers={"User-Agent": UA},
    )
    r.raise_for_status()
    if r.text.strip().startswith("Invalid"):
        raise ValueError(f"HockeyTech rejected the key: {r.text[:60]}")
    return r.json()["SiteKit"]


# Regular seasons are named for the span they cover — "2025-2026 Men's Divisions",
# "2026-27 Men's Divisions". Postseason blocks are separate seasons with their own
# ids and names: "2026 Men's Division 2 Nationals Final Four", "2026 Men's D2
# Regionals". Those ids are issued later, so they outrank the regular season by id
# from March onward — picking the highest id would swap the whole schedule for a
# two-game tournament bracket every spring.
_REGULAR_SEASON_NAME = re.compile(r"^\s*\d{4}-\d{2,4}\s")
_POSTSEASON_WORDS = ("national", "regional", "pool play", "final four", "tournament")


def _current_mens_season(session: requests.Session) -> tuple[str, str]:
    seasons = _ht(session, "seasons")["Seasons"]
    mens = [
        s for s in seasons
        if "women" not in s["season_name"].lower()      # "men" is a substring of "women"
        and "men" in s["season_name"].lower()
        and _REGULAR_SEASON_NAME.match(s["season_name"])
        and not any(w in s["season_name"].lower() for w in _POSTSEASON_WORDS)
    ]
    if not mens:
        raise ValueError("no regular men's season found")
    newest = max(mens, key=lambda s: int(s["season_id"]))
    return newest["season_id"], newest["season_name"]


def fetch_hockey(session: requests.Session, rng: random.Random, today: date) -> dict:
    season_id, season_name = _current_mens_season(session)
    games = _ht(session, "schedule", team_id=OKSTATE_TEAM_ID,
                season_id=season_id).get("Schedule", [])

    played, upcoming = [], []
    for g in games:
        home = g.get("home_team") == OKSTATE_TEAM_ID
        us = int(g.get("home_goal_count") or 0) if home else int(g.get("visiting_goal_count") or 0)
        them = int(g.get("visiting_goal_count") or 0) if home else int(g.get("home_goal_count") or 0)
        opponent = (g.get("visiting_team_name") if home else g.get("home_team_name")) or "?"
        row = {
            "date": g.get("date_played"),
            "opponent": opponent,
            "home": home,
            "us": us, "them": them,
            "status": g.get("game_status"),
        }
        (played if str(g.get("final")) == "1" else upcoming).append(row)

    wins = sum(1 for g in played if g["us"] > g["them"])
    losses = sum(1 for g in played if g["us"] < g["them"])
    ties = len(played) - wins - losses

    # The feed's order is not guaranteed, and a game that never got a final
    # score stays in `upcoming` forever — so sort by date and drop anything
    # already in the past, or "next game" ends up being one from last October.
    def _key(row: dict) -> str:
        return row.get("date") or ""

    played.sort(key=_key)
    upcoming = sorted(
        (g for g in upcoming if _key(g) >= today.isoformat()), key=_key
    )

    nxt = upcoming[0] if upcoming else None
    days_until = None
    if nxt and nxt.get("date"):
        try:
            days_until = (date.fromisoformat(nxt["date"]) - today).days
        except ValueError:
            pass

    return {
        "season": season_name,
        "record": f"{wins}-{losses}" + (f"-{ties}" if ties else ""),
        "games_played": len(played),
        "games_scheduled": len(games),
        "last_result": played[-1] if played else None,
        "next_game": nxt,
        "days_until_next_game": days_until,
    }


# ==========================================================================
# 5. Federal Register — one document the government published yesterday
# ==========================================================================
FR_API = "https://www.federalregister.gov/api/v1/documents.json"
# No weekend or federal-holiday issues; a long weekend can be four days.
REGISTER_MAX_LOOKBACK_DAYS = 6


def fetch_register(session: requests.Session, rng: random.Random, day: date) -> dict:
    """One document from the most recent day the Register actually published.

    It doesn't publish on weekends or federal holidays — a Sunday query returns
    a count of zero — so pinning this to yesterday would record the source as
    dead every Sunday and Monday. Walk back to the last day with an issue.
    """
    body, results, last_err = None, [], None
    for back in range(REGISTER_MAX_LOOKBACK_DAYS):
        probe = day - timedelta(days=back)
        try:
            r = session.get(
                FR_API,
                params={
                    "per_page": 100,
                    "order": "newest",
                    "conditions[publication_date][is]": probe.isoformat(),
                    "fields[]": ["title", "type", "abstract", "agencies",
                                 "publication_date", "html_url", "page_length"],
                },
                timeout=REQUEST_TIMEOUT_S,
                headers={"User-Agent": UA},
            )
            # A day with no issue doesn't answer cleanly: Sundays come back with
            # a count of zero, Saturdays with a 503. Both mean "nothing that
            # day", so neither should end the walk back.
            r.raise_for_status()
            body = r.json()
        except Exception as exc:  # noqa: BLE001
            last_err = f"{probe}: {type(exc).__name__}"
            continue
        results = body.get("results") or []
        if results:
            day = probe
            break
    if not results:
        raise ValueError(
            f"nothing published in the {REGISTER_MAX_LOOKBACK_DAYS} days to {day}"
            + (f" (last error {last_err})" if last_err else "")
        )

    doc = rng.choice(results)
    return {
        "day": day.isoformat(),
        "published_that_day": body.get("count"),
        "title": _redact_contacts(doc.get("title") or ""),
        "type": doc.get("type"),
        "agencies": [a.get("name") for a in (doc.get("agencies") or [])][:3],
        "abstract": _redact_contacts(doc.get("abstract") or "")[:500] or None,
        "pages": doc.get("page_length"),
        "url": doc.get("html_url"),
    }


# ==========================================================================
# Roster and assembly
# ==========================================================================
# Each entry: key -> (label, callable taking (session, rng, run_date)).
SOURCES: dict[str, tuple[str, Callable]] = {
    "fsa": (
        "A photograph from the FSA/OWI negatives, 1935-1944",
        lambda s, r, d: fetch_fsa(s, r),
    ),
    "civic": (
        "What Boston reported broken yesterday (311)",
        lambda s, r, d: fetch_civic(s, r, d - timedelta(days=1)),
    ),
    "surplus": (
        "Something a government is selling off",
        lambda s, r, d: fetch_surplus(s, r),
    ),
    "hockey": (
        "Oklahoma State hockey, ACHA Men's Division 2",
        lambda s, r, d: fetch_hockey(s, r, d),
    ),
    "register": (
        "One document from yesterday's Federal Register",
        lambda s, r, d: fetch_register(s, r, d - timedelta(days=1)),
    ),
}

DEFAULT_ROSTER = ["fsa", "civic", "surplus", "hockey", "register"]


def load_roster(roster_file: Path = ROSTER_FILE) -> dict:
    """Roster plus rotation state. Missing or broken file falls back to defaults."""
    if roster_file.exists():
        try:
            data = json.loads(roster_file.read_text())
            if isinstance(data.get("roster"), list) and data["roster"]:
                data.setdefault("retire_every_builds", RETIRE_EVERY_BUILDS)
                data.setdefault("builds_this_cycle", 0)
                data.setdefault("retired", [])
                return data
        except Exception as exc:  # noqa: BLE001
            _warn(f"roster.json unreadable ({exc}); using defaults")
    return {
        "roster": list(DEFAULT_ROSTER),
        "retire_every_builds": RETIRE_EVERY_BUILDS,
        "builds_this_cycle": 0,
        "retired": [],
    }


class RetirementError(Exception):
    """Georgia named something that can't be retired."""


def apply_retirement(
    key: str,
    reason: str,
    on: date,
    roster_file: Path = ROSTER_FILE,
) -> dict:
    """Retire an input for good and open a new cycle.

    This is binding. Georgia chooses and the source is gone — the roster
    shrinks, the choice goes on the record with her reason, and the countdown
    resets. Nothing else in the pipeline can put it back; only a human editing
    roster.json can, which is the point.
    """
    state = load_roster(roster_file)
    key = (key or "").strip()
    if key not in state["roster"]:
        raise RetirementError(
            f"{key!r} is not on the roster ({', '.join(state['roster'])})"
        )
    if len(state["roster"]) <= 1:
        raise RetirementError("refusing to empty the roster")

    state["roster"] = [k for k in state["roster"] if k != key]
    state.setdefault("retired", []).append({
        "key": key,
        "date": on.isoformat(),
        "reason": re.sub(r"\s+", " ", reason or "").strip()[:500] or None,
    })
    state["builds_this_cycle"] = 0
    state["overdue_builds"] = 0
    state["cycles_completed"] = int(state.get("cycles_completed", 0)) + 1
    state.setdefault("full_roster_size", len(DEFAULT_ROSTER))
    roster_file.parent.mkdir(parents=True, exist_ok=True)
    roster_file.write_text(json.dumps(state, indent=2) + "\n")
    return state


def retirement_from_diary(diary: str) -> tuple[str, str] | None:
    """Read `retiring: <key>` out of the log entry's YAML frontmatter.

    The diary already carries frontmatter that the pipeline parses on the way
    back in, so the declaration rides along with it rather than needing a third
    output tag. Returns (key, reason) or None if she didn't declare one.
    """
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n?(.*)", diary or "", re.S)
    if not m:
        return None
    front, body = m.group(1), m.group(2)
    key = re.search(r"^\s*retiring\s*:\s*[\"']?([a-z0-9_]+)[\"']?\s*$", front, re.M | re.I)
    if not key:
        return None
    return key.group(1).strip(), body.strip()


def fetch_all(run_date: date, roster: list[str], seed: int | None = None) -> dict:
    """Fetch every source on the roster. Failures are recorded, never raised."""
    rng = random.Random(seed)
    session = requests.Session()
    inputs: dict[str, Any] = {}
    failures: dict[str, str] = {}

    for key in roster:
        entry = SOURCES.get(key)
        if entry is None:
            failures[key] = "unknown source key"
            continue
        label, fn = entry
        try:
            inputs[key] = {"label": label, "data": fn(session, rng, run_date)}
        except Exception as exc:  # noqa: BLE001 — one dead source must not stop the rest
            failures[key] = f"{type(exc).__name__}: {exc}"[:200]
            _warn(f"{key}: {failures[key]}")

    return {
        "date": run_date.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": inputs,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch Georgia's daily inputs.")
    ap.add_argument("--date", help="ISO run date (default: today, UTC)")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--only", help="comma-separated source keys")
    args = ap.parse_args(argv)

    run_date = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
    state = load_roster()
    roster = args.only.split(",") if args.only else state["roster"]

    payload = fetch_all(run_date, roster, seed=args.seed)

    # Surface the retirement countdown so Georgia can see it coming.
    if not args.only:
        every = int(state["retire_every_builds"])
        # The cycle does NOT roll over on its own. It ends when a retirement is
        # actually applied (apply_retirement resets the counter), so if she is
        # asked to retire something and doesn't, the demand stands tomorrow and
        # the day after. The countdown is a commitment, not a reminder.
        state["builds_this_cycle"] = min(
            int(state.get("builds_this_cycle", 0)) + 1, every
        )
        overdue = int(state.get("overdue_builds", 0))
        if state["builds_this_cycle"] >= every:
            overdue += 1
        state["overdue_builds"] = overdue if state["builds_this_cycle"] >= every else 0

        payload["rotation"] = {
            "builds_this_cycle": state["builds_this_cycle"],
            "builds_until_retirement": max(0, every - state["builds_this_cycle"]),
            "overdue_builds": state["overdue_builds"],
            "cycles_completed": state.get("cycles_completed", 0),
            "roster_size": len(roster),
            "full_roster_size": int(state.get("full_roster_size", len(DEFAULT_ROSTER))),
            "retired": state.get("retired", []),
        }
        if state["overdue_builds"] > 1:
            _warn(f"retirement overdue for {state['overdue_builds']} builds")
        if len(roster) < payload["rotation"]["full_roster_size"]:
            _warn(
                f"roster is down to {len(roster)} — a replacement fetcher is owed. "
                f"Retired so far: {[r.get('key') for r in state.get('retired', [])]}"
            )

    if args.dry_run or args.only:
        # --only fetches a subset, so writing it would clobber the day's file
        # with a partial payload and drop the rotation block.
        if args.only and not args.dry_run:
            _warn("--only is diagnostic; printing instead of writing")
        print(json.dumps(payload, indent=2))
        return 0

    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out = INPUTS_DIR / f"{run_date.isoformat()}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    if not args.only:
        state["roster"] = roster
        ROSTER_FILE.write_text(json.dumps(state, indent=2) + "\n")

    got, lost = len(payload["inputs"]), len(payload["failures"])
    print(f"fetch_daily_inputs: wrote {out.name} — {got} fetched, {lost} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
