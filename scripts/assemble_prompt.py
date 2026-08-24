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
from typing import Any, Iterable, Sequence

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

# How many days of her own verdicts come back to her. Every prior verdict on a
# frame shown *today* is included regardless of age — being shown what you
# thought of this exact image four months ago is the entire point of the anchors.
TASTE_WINDOW_DAYS = 30
TASTE_MIN_ENTRIES = 3
TASTE_MAX_ENTRIES = 8

# Frames were shown, and she has never written a verdict about any of them.
# NOT the site's day 1 — she will be around 120 days into the project the first
# time this fires, so it may claim only that the images are new, not that she is.
DAY_1_TASTE_SENTINEL = (
    "[taste]\n"
    "These images are new. Not you — them. There is no preference log yet because "
    "you have never looked at any of this before, so there is nothing here to "
    "agree or disagree with. Whatever you write about them today becomes the thing "
    "tomorrow-you gets measured against.\n"
    "[/taste]"
)

# story_016's fail-open path caught a missing manifest, malformed JSON or a dead
# file_id. Say so plainly: she will write about the silence either way, and it is
# better that she knows which silence it is.
CORPUS_DARK_SENTINEL = (
    "[taste]\n"
    "The shelf didn't load today. No frames reached you — the manifest was missing "
    "or something in it had gone stale. This is a pipeline fault, not a decision, "
    "and it should be back tomorrow. You are working without it today.\n"
    "[/taste]"
)

TASTE_TASK = """3. Write your verdicts on today's frames. Output inside <taste>...</taste> tags,
   one JSON object per line, nothing else between the tags:

   {"frame_id": "bd-2014-t152", "verdict": "...", "compared_to": "cad-1987-t201", "confidence": 3}

   Between %(lo)d and %(hi)d lines, each about a frame you were actually shown above.
   `verdict` is prose, in your voice. `confidence` is 1-5. `compared_to` is another
   frame_id or null — optional, but preference forms at boundaries, so a comparison
   is worth more than an isolated reaction.

   Pick the ones that struck you today. Say what you think, and say it in a way a
   stranger could disagree with. Not description — verdict. "Wide symmetric block,
   pit at the front sideline" tells nobody anything. "The version of this with the
   gap still open is better than the one where it closes" is something you could
   turn out to be wrong about later, which is what makes it worth writing down.

""" % {"lo": TASTE_MIN_ENTRIES, "hi": TASTE_MAX_ENTRIES}


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


def load_preferences(path: Path) -> list[dict[str, Any]]:
    """Every entry in corpus/preferences.jsonl, oldest first. Never raises.

    A broken preference log is a reason to show her less history, never a reason
    for the day not to ship, so unparseable lines are skipped rather than fatal.
    """
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        raw_lines = path.read_text().splitlines()
    except OSError as exc:
        _warn(f"preferences.jsonl unreadable ({exc}); continuing without history")
        return []
    for n, raw in enumerate(raw_lines, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            _warn(f"preferences.jsonl line {n} is not JSON; skipping")
            continue
        if isinstance(entry, dict) and entry.get("frame_id"):
            entries.append(entry)
    return entries


def _entry_date(entry: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(entry.get("date", "")))
    except ValueError:
        return None


def build_taste_block(
    entries: list[dict[str, Any]],
    shown_frame_ids: Sequence[str],
    run_date: date,
    window_days: int = TASTE_WINDOW_DAYS,
) -> str:
    """Her own preference log, fed back.

    Two selections, unioned: the last `window_days` of everything, plus *every*
    prior verdict on a frame in front of her today however old. The second is the
    one that matters — an anchor she has written about for four months is the only
    place drift can show up, and withholding any of it was considered and rejected
    (story_018).
    """
    if not shown_frame_ids:
        return CORPUS_DARK_SENTINEL
    if not entries:
        return DAY_1_TASTE_SENTINEL

    shown = set(shown_frame_ids)
    cutoff = run_date - timedelta(days=window_days)
    selected = [
        e for e in entries
        if e.get("frame_id") in shown
        or ((_entry_date(e) or date.min) >= cutoff)
    ]
    if not selected:
        # Entries exist but none is recent and none is about today's frames. Not a
        # beginning and not a fault, so no sentinel — just an empty history.
        return (
            "[taste]\n"
            "Your preference log has entries, but none from the last "
            f"{window_days} days and none about anything you are looking at today.\n"
            "[/taste]"
        )

    on_view = [e for e in selected if e.get("frame_id") in shown]
    other = [e for e in selected if e.get("frame_id") not in shown]

    parts = ["[taste]", "What you have said before, in your own words."]
    if on_view:
        parts.append("")
        parts.append("About frames in front of you right now:")
        parts.append("")
        parts += [_format_preference(e) for e in on_view]
    if other:
        parts.append("")
        parts.append(f"Recent verdicts on other frames (last {window_days} days):")
        parts.append("")
        parts += [_format_preference(e) for e in other]
    parts.append("[/taste]")
    return "\n".join(parts)


def _format_preference(entry: dict[str, Any]) -> str:
    when = entry.get("date") or "undated"
    frame = entry.get("frame_id", "?")
    confidence = entry.get("confidence")
    against = entry.get("compared_to")
    bits = [f"- {when} {frame}"]
    if confidence is not None:
        bits.append(f"(confidence {confidence})")
    if against:
        bits.append(f"[against {against}]")
    return " ".join(bits) + f": {entry.get('verdict', '')}"


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


def assemble_prompt(
    run_date: date,
    repo_root: Path = REPO_ROOT,
    shown_frame_ids: Sequence[str] | None = None,
) -> str:
    """Assemble the day's text prompt.

    `shown_frame_ids` is the ordered frame-id list story_016's `select_for_date`
    returns, threaded here because the prompt depends on what was selected: it
    needs the ids to look up prior verdicts, and it needs to know whether *any*
    frame was shown to choose between the two corpus sentinels. Selection
    therefore runs BEFORE assembly, not after.

    The default is `None`, meaning "no corpus in play at all" — every existing
    caller keeps working untouched and the text-only path stays the natural
    default rather than a special case. That is deliberately distinct from an
    empty sequence, which means the corpus was expected and failed to load, and
    gets CORPUS_DARK_SENTINEL.
    """
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

    today_str = run_date.isoformat()
    project_checklist = _project_checklist_line(facts)
    archive_note = _archive_url_note(repo_root / "archive")

    # No corpus in play: no block, no task, exactly the prompt that shipped before
    # the corpus existed.
    taste_block = ""
    taste_task = ""
    if shown_frame_ids is not None:
        preferences = load_preferences(repo_root / "corpus" / "preferences.jsonl")
        taste_block = build_taste_block(preferences, shown_frame_ids, run_date) + "\n\n---\n"
        # On a dark day, do not ask for verdicts about frames she was never shown.
        # Validation already drops entries naming frames absent from the selection,
        # so asking anyway would reliably generate warnings for output we requested.
        if shown_frame_ids:
            taste_task = TASTE_TASK

    return f"""You are Georgia. Read this carefully.

{soul}

These are the facts about Jeff. They are inviolable — every version of the site must include them, however creatively presented.

```json
{facts_raw}
```

---

{history_block}

---

{feedback_block}

---

{taste_block}
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

{taste_task}
Remember: the facts above are inviolable. Everything else — tone, design, copy, structure — is yours.
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
