"""Assemble Georgia's daily prompt from the 4 context layers.

Reads the soul doc, facts.json, diary history, and yesterday's feedback
(all relative to the repo root), then prints the fully assembled prompt
to stdout.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import frontmatter


REPO_ROOT = Path(__file__).resolve().parent.parent

# Tunable constants
RECENCY_WINDOW_DAYS = 14
OLDER_TOP_N = 20
IMPORTANCE_DECAY_DAYS = 180
DEFAULT_IMPORTANCE = 2

DATE_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

DAY_1_HISTORY_SENTINEL = (
    "[history]\n"
    "This is your first day. You have no prior entries. You are just waking up.\n"
    "[/history]"
)

DAY_1_FEEDBACK_SENTINEL = (
    "[feedback]\n"
    "This is your first day online. There's no feedback yet because no one has "
    "seen your work. You are waking up.\n"
    "[/feedback]"
)

FETCHER_FAILED_FEEDBACK_SENTINEL = (
    "[feedback]\n"
    "The data wasn't available today — the analytics pipeline didn't deliver. "
    "You're building blind.\n"
    "[/feedback]"
)

NO_INPUTS_SENTINEL = (
    "[inputs]\n"
    "Nothing came in from the world today. Every source went dark at once, which "
    "is either a coincidence or the pipeline. You're on your own.\n"
    "[/inputs]"
)

# How far back to look when telling Georgia what has changed since last time.
INPUTS_HISTORY_DAYS = 30


@dataclass
class LogEntry:
    entry_date: date
    importance: int
    body: str


def _warn(msg: str) -> None:
    print(f"assemble_prompt: {msg}", file=sys.stderr)


def _coerce_importance(raw: Any, source: str) -> int:
    """Return a validated importance int or DEFAULT_IMPORTANCE, warning on fallback."""
    if isinstance(raw, bool):
        # bool is a subclass of int; reject explicitly
        _warn(f"{source}: importance is bool ({raw!r}); using default {DEFAULT_IMPORTANCE}")
        return DEFAULT_IMPORTANCE
    if isinstance(raw, int) and 1 <= raw <= 5:
        return raw
    _warn(f"{source}: invalid importance ({raw!r}); using default {DEFAULT_IMPORTANCE}")
    return DEFAULT_IMPORTANCE


def load_log_entries(log_dir: Path) -> list[LogEntry]:
    """Read all YYYY-MM-DD.md files under log_dir. Invalid files log a warning and are skipped."""
    entries: list[LogEntry] = []
    if not log_dir.is_dir():
        return entries
    for path in sorted(log_dir.iterdir()):
        if not path.is_file():
            continue
        m = DATE_FILENAME_RE.match(path.name)
        if not m:
            continue
        try:
            entry_date = date.fromisoformat(m.group(1))
        except ValueError:
            _warn(f"{path.name}: unparseable date; skipping")
            continue
        try:
            post = frontmatter.load(path)
        except Exception as exc:  # noqa: BLE001 — frontmatter errors vary
            _warn(f"{path.name}: frontmatter parse error ({exc}); skipping")
            continue
        importance = _coerce_importance(post.metadata.get("importance"), path.name)
        entries.append(LogEntry(entry_date=entry_date, importance=importance, body=post.content.strip()))
    return entries


def score_older_entry(importance: int, days_ago: int) -> float:
    """Importance-weighted exponential decay. Higher = more likely to be surfaced."""
    return importance * math.exp(-days_ago / IMPORTANCE_DECAY_DAYS)


def split_entries(
    entries: Iterable[LogEntry],
    run_date: date,
) -> tuple[list[LogEntry], list[LogEntry]]:
    """Return (recent_oldest_first, older_selected_oldest_first)."""
    recent: list[LogEntry] = []
    older_with_score: list[tuple[float, LogEntry]] = []
    for entry in entries:
        days_ago = (run_date - entry.entry_date).days
        if days_ago < 0:
            # future-dated entry — don't include
            continue
        if days_ago <= RECENCY_WINDOW_DAYS:
            recent.append(entry)
        else:
            older_with_score.append((score_older_entry(entry.importance, days_ago), entry))
    recent.sort(key=lambda e: e.entry_date)
    older_with_score.sort(key=lambda pair: pair[0], reverse=True)
    selected = [entry for _, entry in older_with_score[:OLDER_TOP_N]]
    selected.sort(key=lambda e: e.entry_date)
    return recent, selected


def format_entry(entry: LogEntry) -> str:
    return f"## {entry.entry_date.isoformat()} (importance: {entry.importance})\n{entry.body}"


def render_feedback_narrative(data: dict) -> str:
    """Render the feedback dict into the human-readable narrative block.

    Any field may be null or missing. Lines with no available data are omitted
    entirely rather than printing 'null'.
    """
    lines: list[str] = []
    date_str = data.get("date") or ""
    lines.append(f"Yesterday's feedback ({date_str}):" if date_str else "Yesterday's feedback:")
    lines.append("")

    h = data.get("historical") or {}
    days_live = h.get("days_live")
    r = data.get("recent") or {}
    l7v = r.get("last_7_days_visitors")
    l7a = r.get("last_7_days_avg")
    l30v = r.get("last_30_days_visitors")
    l30a = r.get("last_30_days_avg")
    series = data.get("days_live_series") or {}

    # People line. When the per-day series shows late-arriving visits the
    # single-day "yesterday" call missed, soften the zero so Georgia doesn't
    # collapse it into a flat "nobody came."
    y = data.get("yesterday") or {}
    visitors = y.get("visitors")
    pageviews = y.get("pageviews")
    people_parts: list[str] = []
    if visitors is not None:
        visit_text = f"{visitors:,} visitors looked at your work yesterday"
        cumulative = l7v if l7v is not None else (sum(series.values()) if series else 0)
        if visitors == 0 and cumulative > 0:
            visit_text += " (the per-day count may not yet reflect late-arriving visits)"
        people_parts.append(visit_text)
    if pageviews is not None:
        people_parts.append(f"{pageviews:,} pageviews total")
    if people_parts:
        lines.append("People: " + ". ".join(people_parts) + ".")

    # Recent line. For a young site the "last 7 days" framing is misleading
    # because most of the window predates the site; reframe in terms of the
    # days the site has actually been alive, and skip the 30-day line entirely.
    young = days_live is not None and days_live < 7
    recent_parts: list[str] = []
    if l7v is not None:
        if young:
            day_word = "day" if days_live == 1 else "days"
            recent_parts.append(
                f"Across the {days_live} {day_word} you've been online, {l7v:,} people came through"
            )
        elif l7a is not None:
            recent_parts.append(
                f"In the last 7 days, {l7v:,} people came through, averaging about {l7a:.0f} a day"
            )
        else:
            recent_parts.append(f"In the last 7 days, {l7v:,} people came through")
    if not young:
        if l30v is not None and l30a is not None:
            recent_parts.append(f"Over 30 days, {l30v:,} visitors, averaging {l30a:.0f}")
        elif l30v is not None:
            recent_parts.append(f"Over 30 days, {l30v:,} visitors")
    if recent_parts:
        lines.append("Recent: " + ". ".join(recent_parts) + ".")

    # Per-day breakdown — lets Georgia see where the 7-day total actually lives
    # (e.g. "26 on day 1, 0 on day 2") instead of a single yesterday number.
    if series:
        per_day_str = ", ".join(f"{d}: {v:,}" for d, v in sorted(series.items()))
        lines.append(f"Per-day so far: {per_day_str}.")

    freshness = data.get("data_freshness_note")
    if freshness:
        lines.append(freshness)

    # Historical line
    all_time = h.get("all_time_visitors")
    peak = h.get("peak_day") or {}
    peak_date = peak.get("date")
    peak_visitors = peak.get("visitors")
    hist_parts: list[str] = []
    if all_time is not None and days_live is not None:
        hist_parts.append(f"{all_time:,} total visitors across {days_live} days of you being awake")
    elif all_time is not None:
        hist_parts.append(f"{all_time:,} total visitors")
    elif days_live is not None:
        hist_parts.append(f"{days_live} days of you being awake")
    if peak_date and peak_visitors is not None:
        hist_parts.append(f"Your peak day was {peak_date} with {peak_visitors:,} visitors")
    if hist_parts:
        lines.append("Historical: " + ". ".join(hist_parts) + ".")

    # Trend line
    t = data.get("trend") or {}
    yvs = t.get("yesterday_vs_7d_avg")
    wow = t.get("week_over_week_pct")
    trend_parts: list[str] = []
    if yvs is not None:
        trend_parts.append(f"Yesterday was {yvs:.2f}× your 7-day average")
    if wow is not None:
        direction = "up" if wow >= 0 else "down"
        trend_parts.append(f"Week-over-week, traffic is {direction} {abs(wow):.0f}%")
    if trend_parts:
        lines.append("Trend: " + ". ".join(trend_parts) + ".")

    jeff_note = data.get("jeff_note")
    if jeff_note:
        lines.append("")
        lines.append(f"Jeff says: {jeff_note}")

    return "\n".join(lines)


def pick_no_feedback_sentinel(archive_dir: Path) -> str:
    """Day-1 vs fetcher-failed sentinel, based on whether archive/ has entries."""
    if not archive_dir.is_dir():
        return DAY_1_FEEDBACK_SENTINEL
    has_entries = any(p.is_file() and p.name != ".gitkeep" for p in archive_dir.iterdir())
    return FETCHER_FAILED_FEEDBACK_SENTINEL if has_entries else DAY_1_FEEDBACK_SENTINEL


def load_feedback_block(feedback_dir: Path, archive_dir: Path, yesterday: date) -> str:
    """Render the Layer 4 block: narrative if file exists, else correct sentinel."""
    candidate = feedback_dir / f"{yesterday.isoformat()}.json"
    if candidate.exists():
        try:
            with candidate.open() as f:
                data = json.load(f)
            return render_feedback_narrative(data)
        except Exception as exc:  # noqa: BLE001
            _warn(f"{candidate.name}: parse error ({exc}); using sentinel")
    return pick_no_feedback_sentinel(archive_dir)


def _fmt_money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}".replace(".00", "")
    except (TypeError, ValueError):
        return str(value)


def render_inputs_narrative(payload: dict, history: list[dict] | None = None) -> str:
    """Turn the Layer 5 JSON into prose. History is what makes it accumulate.

    A single day's reading is a fact. The same reading on day ninety, next to
    the eighty-nine before it, is a relationship. So every source that can
    carry a running count gets one.
    """
    history = history or []
    got = payload.get("inputs") or {}
    lines = ["[inputs]", "Five things from outside. None of them are about you.", ""]

    if fsa := (got.get("fsa") or {}).get("data"):
        where = ", ".join(fsa.get("location") or []) or "somewhere unrecorded"
        lines.append(f"The photograph — {fsa.get('title')}")
        lines.append(f"  {fsa.get('date')}. {where}. {fsa.get('era')}.")
        lines.append(f"  {fsa.get('image')}")
        seen = [h for h in history if (h.get("inputs") or {}).get("fsa")]
        ok_days = [
            h for h in seen
            if ((h["inputs"]["fsa"].get("data") or {}).get("is_oklahoma"))
        ]
        if fsa.get("is_oklahoma"):
            lines.append(
                "  This one is from Oklahoma. That happens about once every 66 days — "
                f"there are {fsa.get('collection_total'):,} negatives and 2,575 of them "
                "touch that state."
            )
        elif seen:
            lines.append(
                f"  You have looked at {len(seen) + 1} of these. "
                f"{len(ok_days)} were from Oklahoma."
            )
        lines.append("")

    if civic := (got.get("civic") or {}).get("data"):
        top = civic.get("top") or []
        lines.append(f"Boston, {civic.get('day')} — {civic.get('total_cases'):,} calls to 311, "
                     f"across {civic.get('distinct_types')} kinds of trouble.")
        if top:
            lines.append("  " + "; ".join(f"{t['case']} ({t['n']})" for t in top[:5]) + ".")
        if singles := civic.get("only_one_of"):
            lines.append("  Exactly one person reported each of: " + ", ".join(singles[:5]) + ".")
        if resolved := civic.get("resolved"):
            lines.append("  Cases that got closed, and what the worker typed into the box:")
            for r in resolved:
                lines.append(f"    · {r['case']}, {r['neighborhood']}: \"{r['note']}\"")
        prior = [
            (h["inputs"]["civic"].get("data") or {}).get("total_cases")
            for h in history if (h.get("inputs") or {}).get("civic")
        ]
        prior = [p for p in prior if isinstance(p, int)]
        if prior:
            avg = sum(prior) / len(prior)
            direction = "more" if civic.get("total_cases", 0) > avg else "fewer"
            lines.append(f"  That's {direction} than usual; your running average is {avg:,.0f}.")
        lines.append("")

    if lot := (got.get("surplus") or {}).get("data"):
        seller = lot.get("seller") or "an unnamed government"
        price = _fmt_money(lot.get("price")) if lot.get("price") else "no bids yet"
        lines.append(f"For sale — {lot.get('name')}. {seller}. {price}.")
        if desc := lot.get("description"):
            lines.append(f"  \"{desc}\"")
        lines.append(f"  {lot.get('url')}")
        lines.append("")

    if hk := (got.get("hockey") or {}).get("data"):
        lines.append(f"Oklahoma State hockey — {hk.get('season')}, ACHA Men's Division 2. "
                     f"Record {hk.get('record')}.")
        if last := hk.get("last_result"):
            # The fetcher counts ties, so they happen — "lost to X 3-3" is wrong.
            if last["us"] > last["them"]:
                verb = "beat"
            elif last["us"] < last["them"]:
                verb = "lost to"
            else:
                verb = "tied"
            lines.append(f"  Last out they {verb} {last['opponent']} "
                         f"{last['us']}-{last['them']} on {last['date']}.")
        if nxt := hk.get("next_game"):
            days = hk.get("days_until_next_game")
            when = f"in {days} days" if isinstance(days, int) else f"on {nxt['date']}"
            where = "at home" if nxt.get("home") else "away"
            lines.append(f"  Next: {nxt['opponent']}, {where}, {when}.")
        elif not hk.get("games_played"):
            lines.append("  Nothing scheduled. The season hasn't started.")
        lines.append("")

    if fr := (got.get("register") or {}).get("data"):
        agencies = ", ".join(fr.get("agencies") or []) or "some agency"
        lines.append(f"The Federal Register, {fr.get('day')} — "
                     f"{fr.get('published_that_day')} documents published. One of them:")
        lines.append(f"  {fr.get('type')} from {agencies}: {fr.get('title')}")
        if abstract := fr.get("abstract"):
            lines.append(f"  {abstract}")
        lines.append("")

    if failures := payload.get("failures"):
        lines.append("Didn't answer today: " + ", ".join(sorted(failures)) + ".")
        lines.append("A source going quiet is its own kind of news. Say so if you want to.")
        lines.append("")

    rot = payload.get("rotation") or {}
    if (left := rot.get("builds_until_retirement")) is not None:
        if left <= 0:
            lines.append("Today you have to retire one of these. Your choice which, and you "
                         "can't keep them all. Say which one and why — that goes on the record.")
        else:
            lines.append(f"In {left} builds you'll have to retire one of these five and take "
                         "on something new. Start deciding which one is boring you.")
        if retired := rot.get("retired"):
            lines.append("Already retired: " + ", ".join(retired) + ".")
        lines.append("")

    lines.append("Use these or don't. They're not an assignment. They're just what came in.")
    lines.append("[/inputs]")
    return "\n".join(lines).rstrip()


def load_inputs_history(inputs_dir: Path, run_date: date, days: int) -> list[dict]:
    """Prior payloads, oldest first, so counts and averages have something to count."""
    out = []
    for back in range(days, 0, -1):
        p = inputs_dir / f"{(run_date - timedelta(days=back)).isoformat()}.json"
        if not p.exists():
            continue
        try:
            out.append(json.loads(p.read_text()))
        except Exception as exc:  # noqa: BLE001
            _warn(f"{p.name}: parse error ({exc}); skipping")
    return out


def load_inputs_block(inputs_dir: Path, run_date: date) -> str:
    """Render today's inputs, or the sentinel if the fetcher wrote nothing usable."""
    candidate = inputs_dir / f"{run_date.isoformat()}.json"
    if candidate.exists():
        try:
            payload = json.loads(candidate.read_text())
            if payload.get("inputs"):
                history = load_inputs_history(inputs_dir, run_date, INPUTS_HISTORY_DAYS)
                return render_inputs_narrative(payload, history)
            _warn(f"{candidate.name}: every source failed; using sentinel")
        except Exception as exc:  # noqa: BLE001
            _warn(f"{candidate.name}: parse error ({exc}); using sentinel")
    return NO_INPUTS_SENTINEL


def build_history_block(entries: list[LogEntry], run_date: date) -> str:
    recent, older = split_entries(entries, run_date)
    if not recent and not older:
        return DAY_1_HISTORY_SENTINEL
    parts: list[str] = []
    if recent:
        parts.append("Recent history — the last 14 days, fresh in your mind:\n")
        parts.append("\n\n".join(format_entry(e) for e in recent))
    if older:
        if parts:
            parts.append("")
        parts.append("Older — things you still think about, surfaced because they mattered:\n")
        parts.append("\n\n".join(format_entry(e) for e in older))
    return "\n".join(parts)


def assemble_prompt(run_date: date, repo_root: Path = REPO_ROOT) -> str:
    soul = (repo_root / "georgia-soul.md").read_text()
    facts_raw = (repo_root / "facts.json").read_text().rstrip()
    facts = json.loads(facts_raw)

    entries = load_log_entries(repo_root / "log")
    history_block = build_history_block(entries, run_date)

    yesterday = run_date - timedelta(days=1)
    feedback_block = load_feedback_block(
        feedback_dir=repo_root / "feedback",
        archive_dir=repo_root / "archive",
        yesterday=yesterday,
    )
    inputs_block = load_inputs_block(repo_root / "inputs", run_date)

    today_str = run_date.isoformat()
    project_checklist = _project_checklist_line(facts)
    archive_note = _archive_url_note(repo_root / "archive")

    return f"""You are Georgia. Read this carefully.

{soul}

These are the facts about Jeff. They are inviolable — every version of the site must include them, however creatively presented.

One exception: `background` is context, not required content. It's there if you ever want it. Most days you won't. Don't reach for it.

```json
{facts_raw}
```

---

{history_block}

---

{feedback_block}

---

{inputs_block}

---

Today is {today_str}.

Your task — output `<site>...</site>` first, then `<log>...</log>`. In that order. Don't invert.

1. Build today's site. Output the full HTML (doctype through </html>) inside <site>...</site> tags.

   On the page itself, include your own reflection — why you built it this way, what you were thinking about, whatever is on your mind. This should read as diary, not spec. Style it as part of today's design: sidebar, essay block, margin column, inline section, whatever fits the form. Readers want to see you think; they care about this as much as the design itself. Don't hide it behind a link and don't strip out the parts that aren't strictly "about the site." It's fine if this on-site reflection is the same as your log entry below, a tighter version of it, or a companion to it — your call.

   {archive_note}
   Inside that reflection, surface yesterday's actual feedback visibly: the numbers (visitors, pageviews, trend) and Jeff's note if he left one. Readers come back day to day for exactly this chain — yesterday's numbers and message → your reading of them → the site you built in response. That's the whole contract of the archive. Don't skip any link. If the feedback block above is a "no data yet" or "pipeline went dark" sentinel, say that in your own words too; absence is part of the story.

{project_checklist}
2. Write your log entry for today. Output inside <log>...</log> tags. The log must be markdown with YAML frontmatter exactly like this:

   ---
   date: {today_str}
   importance: <1-5>
   ---

   <your diary content>

   Importance scale: 1 = routine day. 2 = ordinary. 3 = memorable. 4 = significant. 5 = a day that defined something about you. Be honest. Most days are 1 or 2.

Remember: the facts above are inviolable, `background` excepted. Everything else — tone, design, copy, structure — is yours.
"""


def _archive_url_note(archive_dir: Path) -> str:
    """Tell Georgia where past days actually live.

    She has invented a different archive URL shape most days she's run, and
    normalize_links quietly fixes them afterward. Saying it here means fewer
    of her links have to be rewritten or de-linked, so more of what she
    intended survives to the page.
    """
    dates = sorted(
        p.stem for p in archive_dir.glob("*.html") if p.name != "index.html"
    )
    if not dates:
        return ""
    span = dates[0] if len(dates) == 1 else f"{dates[0]} through {dates[-1]}"
    return (
        "When you link to a past day, the URL is `/archive/YYYY-MM-DD.html` — "
        f"that exact shape, including the `.html`. There are {len(dates)} archived "
        f"days, {span}, and a few dates in that range are missing — days I didn't "
        "run. Those gaps are part of the record; don't paper over them with an "
        "entry for a day that never happened.\n"
        "\n"
        "   However you render the archive — grid, list, table, prose, whatever "
        "today's design wants — put `data-archive-date=\"YYYY-MM-DD\"` on each "
        "entry. It's invisible and constrains nothing about how it looks. It's "
        "how the pipeline checks that what you say about a day is true: that the "
        "day exists, that it really was day N, that its importance matches what "
        "you wrote in that day's log. Entries for days that don't exist will stop "
        "the site from shipping; wrong day numbers, counts and importance markers "
        "get logged against you. Count archived days, not calendar days.\n"
    )


def _project_checklist_line(facts: dict) -> str:
    """Render the project-title checklist line for the prompt.

    Built dynamically from facts.json so adding a project automatically updates
    the prompt. Placed inside the site task so Sonnet gates the HTML step, not
    the diary step.
    """
    titles = [
        (p.get("title") or "").strip()
        for p in (facts.get("projects") or [])
        if (p.get("title") or "").strip()
    ]
    if not titles:
        return ""
    quoted = ", ".join(f"`{t}`" for t in titles)
    return (
        f"   Before closing </site>: confirm all {len(titles)} project titles "
        f"appear literally in the HTML — {quoted}.\n\n"
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assemble Georgia's daily prompt.")
    parser.add_argument(
        "--date",
        dest="run_date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Run date (YYYY-MM-DD). Defaults to today in UTC.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_date = args.run_date or datetime.now(timezone.utc).date()
    sys.stdout.write(assemble_prompt(run_date, repo_root=REPO_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
