"""Render stats.html from stats.jsonl.

Plain HTML, inline CSS, no JavaScript, no external assets. Shows a rolling
window summary plus the last WINDOW entries in reverse-chron order, with
failed runs visually distinct.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

from scripts.call_model import MAX_TOKENS


REPO_ROOT = Path(__file__).resolve().parent.parent
WINDOW = 30
FAILURE_PREVIEW_CHARS = 50
WARNING_PREVIEW_CHARS = 60

# The four outcomes scripts/corpus/consistency.py writes. Spelled out here rather
# than imported: story_018 is offline analysis and must not be importable from the
# daily pipeline, and this module is in it. The coupling is the file format.
DRIFT_OUTCOMES = ("consistent", "evolved", "reversed", "unrelated")


def _read_entries(stats_file: Path) -> list[dict]:
    entries: list[dict] = []
    if not stats_file.exists():
        return entries
    for raw in stats_file.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return entries


def _summarize(window: list[dict]) -> dict:
    total = len(window)
    if total == 0:
        return {
            "runs_total": 0,
            "first_try_success_pct": 0.0,
            "overall_commit_pct": 0.0,
            "avg_duration_s": 0.0,
            "runs_with_warnings": 0,
            "peak_output_tokens": 0,
            "peak_output_pct": 0.0,
        }
    first_try_wins = sum(1 for e in window if e.get("attempts") == 1 and e.get("committed"))
    commits = sum(1 for e in window if e.get("committed"))
    total_duration_ms = sum(e.get("duration_ms", 0) for e in window)
    warned = sum(
        1 for e in window if e.get("archive_warnings") or e.get("corpus_warnings")
    )
    # Peak, not average: max_tokens is a ceiling, so the worst day is the one
    # that decides whether the next prompt growth truncates a page.
    peak_output = max((e.get("output_tokens", 0) for e in window), default=0)
    return {
        "runs_total": total,
        "first_try_success_pct": round(first_try_wins / total * 100, 1),
        "overall_commit_pct": round(commits / total * 100, 1),
        "avg_duration_s": round(total_duration_ms / 1000 / total, 2),
        "runs_with_warnings": warned,
        "peak_output_tokens": peak_output,
        "peak_output_pct": round(peak_output / MAX_TOKENS * 100, 1),
    }


def _row_html(entry: dict) -> str:
    committed = bool(entry.get("committed"))
    row_class = "ok" if committed else "fail"
    status = "✓" if committed else "✗"
    failures = " | ".join(entry.get("validation_failures") or [])
    if len(failures) > FAILURE_PREVIEW_CHARS:
        failures = failures[: FAILURE_PREVIEW_CHARS - 1] + "…"
    # Two different kinds of "shipped anyway": the page making a false claim
    # about the archive, and the corpus misbehaving. Both belong in the same
    # column; lines written before either key existed simply have neither.
    warnings = (entry.get("archive_warnings") or []) + (entry.get("corpus_warnings") or [])
    if warnings:
        preview = " | ".join(warnings)
        if len(preview) > WARNING_PREVIEW_CHARS:
            preview = preview[: WARNING_PREVIEW_CHARS - 1] + "…"
        warning_cell = f"{len(warnings)} · {escape(preview)}"
    else:
        warning_cell = ""
    duration_s = round(entry.get("duration_ms", 0) / 1000, 2)
    out_tokens = entry.get("output_tokens", 0)
    # 0 means no response shipped, or a line written before the key existed.
    token_cell = f"{out_tokens:,}" if out_tokens else ""
    return (
        f'    <tr class="{row_class}">'
        f"<td>{escape(str(entry.get('date', '')))}</td>"
        f"<td>{entry.get('attempts', '')}</td>"
        f"<td>{status}</td>"
        f"<td>{escape(failures)}</td>"
        f'<td class="warn">{warning_cell}</td>'
        f"<td>{entry.get('api_errors', 0)}</td>"
        f"<td>{duration_s}</td>"
        f"<td>{token_cell}</td>"
        "</tr>"
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for raw in path.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _verify_line(root: Path) -> str:
    """What the last corpus verification found, if one has ever run."""
    path = root / "corpus" / "verify.json"
    if not path.exists():
        return ""
    try:
        status = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    when = escape(str(status.get("checked_at", "")))
    if status.get("corpus_verify_failed"):
        missing = ", ".join(escape(str(m)) for m in (status.get("missing") or []))
        return (
            f'<p class="note warnline"><strong>corpus_verify_failed</strong> ({when}): '
            f"these frames no longer resolve — {missing}. The site still ships; the "
            "day they are needed it runs text-only.</p>"
        )
    return f'<p class="note">Corpus verified {when}: every file reference resolves.</p>'


def _drift_block(root: Path) -> str:
    """story_018's read of the record. Renders cleanly with nothing recorded yet.

    Plain and factual on purpose. `consistent` is not the good outcome and
    `reversed` is not failure — a reversal at 90 days may be the most interesting
    thing on this page. Interpreting it is Georgia's job, on the site, not the
    pipeline's.
    """
    records = _read_jsonl(root / "corpus" / "consistency.jsonl")
    counts = {o: sum(1 for r in records if r.get("classification") == o) for o in DRIFT_OUTCOMES}
    longest = max((int(r.get("gap_days") or 0) for r in records), default=0)

    drifted: dict[str, int] = {}
    for r in records:
        if r.get("classification") in ("reversed", "evolved"):
            key = str(r.get("frame_id", "?"))
            drifted[key] = drifted.get(key, 0) + 1
    top = sorted(drifted.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    if not records:
        body = (
            '<p class="note">No pairs yet. A frame needs two verdicts at least 14 days '
            "apart before there is anything to compare.</p>"
        )
    else:
        cards = "".join(
            f'  <div class="card"><span class="k">{o}</span>'
            f'<span class="v">{counts[o]}</span></div>\n'
            for o in DRIFT_OUTCOMES
        )
        body = (
            f'<div class="summary">\n'
            f'  <div class="card"><span class="k">pairs compared</span>'
            f'<span class="v">{len(records)}</span></div>\n'
            f"{cards}"
            f'  <div class="card"><span class="k">longest gap</span>'
            f'<span class="v">{longest}d</span></div>\n'
            f"</div>"
        )
        if top:
            listed = ", ".join(f"{escape(f)} ({n})" for f, n in top)
            body += f'<p class="note">Most movement: {listed}.</p>'

    return (
        "<h2>Corpus</h2>\n"
        + _verify_line(root)
        + '<p class="note">She writes about the same ten anchor frames every day. When two '
        "verdicts on one frame sit at least 14 days apart, they get compared. "
        "<code>consistent</code> is not the good outcome and <code>reversed</code> is not "
        "failure — this is a measurement, not a score.</p>\n"
        + body
    )


def build_stats_page(repo_root: Path | None = None) -> None:
    root = repo_root or REPO_ROOT
    entries = _read_entries(root / "stats.jsonl")
    window = entries[-WINDOW:]
    summary = _summarize(window)

    rows = "\n".join(_row_html(e) for e in reversed(window))
    drift_block = _drift_block(root)

    # Every line predating the token fields reports 0. Showing "0 / 0.0% of the
    # ceiling" would read as a measured zero rather than as no data yet, so the
    # headroom cards only appear once a run has actually recorded a count.
    headroom_cards = ""
    if summary["peak_output_tokens"]:
        headroom_cards = (
            f'  <div class="card"><span class="k">peak output tokens</span>'
            f'<span class="v">{summary["peak_output_tokens"]:,}</span></div>\n'
            f'  <div class="card"><span class="k">of the {MAX_TOKENS:,} ceiling</span>'
            f'<span class="v">{summary["peak_output_pct"]}%</span></div>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Stats — oops-all-vibes</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:60em;margin:2em auto;padding:0 1em;color:#222;}}
h1{{margin-bottom:.2em;}}
table{{border-collapse:collapse;width:100%;font-size:.9em;margin-top:1em;}}
th,td{{border:1px solid #ddd;padding:.4em .6em;text-align:left;vertical-align:top;}}
th{{background:#f6f6f6;}}
tr.fail{{background:#fff0f0;}}
tr.ok{{background:#f0fff4;}}
td.warn{{color:#8a5a00;}}
.summary{{display:flex;gap:1em;flex-wrap:wrap;margin:1em 0;}}
.card{{background:#f6f6f6;padding:.5em 1em;border-radius:4px;min-width:8em;}}
.k{{color:#666;font-size:.8em;display:block;}}
.v{{font-weight:600;font-size:1.2em;}}
a{{color:#0366d6;}}
footer{{margin-top:2em;font-size:.85em;color:#666;}}
p.note{{font-size:.85em;color:#666;max-width:48em;}}
p.warnline{{color:#8a5a00;}}
h2{{margin-top:2em;font-size:1.1em;}}
</style></head>
<body>
<h1>Pipeline stats — oops-all-vibes</h1>
<p>Rolling window: last {summary["runs_total"]} runs (max {WINDOW}).</p>
<p class="note">"Warnings" are things a shipped day got away with. Archive warnings are
claims the page made about the archive that weren't true — a miscounted total, a wrong day
number, an importance that disagrees with that day's log. Claims that would have the site
invent a day that never ran block the run instead, and show as a failure. Corpus warnings
are the shelf misbehaving: <code>corpus_dropped</code> means the day ran text-only because
a frame reference failed, and the rest are <code>&lt;taste&gt;</code> lines that were
skipped. Neither kind stops the site going up.</p>
<p class="note">"Output tokens" counts what the model generated, including the reasoning
it does not return. It shares the {MAX_TOKENS:,}-token ceiling with the page itself, so
the peak figure is the headroom: as the prompt grows the pages grow with it, and a run
that reaches the ceiling gets cut off mid-page and has to retry. Blank means no response
shipped that day.</p>
<div class="summary">
  <div class="card"><span class="k">runs</span><span class="v">{summary["runs_total"]}</span></div>
  <div class="card"><span class="k">first-try success</span><span class="v">{summary["first_try_success_pct"]}%</span></div>
  <div class="card"><span class="k">committed</span><span class="v">{summary["overall_commit_pct"]}%</span></div>
  <div class="card"><span class="k">avg duration</span><span class="v">{summary["avg_duration_s"]}s</span></div>
  <div class="card"><span class="k">runs with warnings</span><span class="v">{summary["runs_with_warnings"]}</span></div>
{headroom_cards}
</div>
<table>
  <thead><tr><th>date</th><th>attempts</th><th>committed</th><th>failures</th><th>warnings</th><th>api errors</th><th>duration (s)</th><th>output tokens</th></tr></thead>
  <tbody>
{rows}
  </tbody>
</table>
{drift_block}
<footer><a href="/">Back to today</a></footer>
</body></html>
"""
    (root / "stats.html").write_text(html)
