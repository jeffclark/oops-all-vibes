"""Render stats.html from stats.jsonl.

Plain HTML, inline CSS, no JavaScript, no external assets. Shows a rolling
window summary plus the last WINDOW entries in reverse-chron order, with
failed runs visually distinct.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
WINDOW = 30
FAILURE_PREVIEW_CHARS = 50


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
        }
    first_try_wins = sum(1 for e in window if e.get("attempts") == 1 and e.get("committed"))
    commits = sum(1 for e in window if e.get("committed"))
    total_duration_ms = sum(e.get("duration_ms", 0) for e in window)
    return {
        "runs_total": total,
        "first_try_success_pct": round(first_try_wins / total * 100, 1),
        "overall_commit_pct": round(commits / total * 100, 1),
        "avg_duration_s": round(total_duration_ms / 1000 / total, 2),
    }


def _row_html(entry: dict) -> str:
    committed = bool(entry.get("committed"))
    row_class = "ok" if committed else "fail"
    status = "✓" if committed else "✗"
    failures = " | ".join(entry.get("validation_failures") or [])
    if len(failures) > FAILURE_PREVIEW_CHARS:
        failures = failures[: FAILURE_PREVIEW_CHARS - 1] + "…"
    duration_s = round(entry.get("duration_ms", 0) / 1000, 2)
    return (
        f'    <tr class="{row_class}">'
        f"<td>{escape(str(entry.get('date', '')))}</td>"
        f"<td>{entry.get('attempts', '')}</td>"
        f"<td>{status}</td>"
        f"<td>{escape(failures)}</td>"
        f"<td>{entry.get('api_errors', 0)}</td>"
        f"<td>{duration_s}</td>"
        "</tr>"
    )


def build_stats_page(repo_root: Path | None = None) -> None:
    root = repo_root or REPO_ROOT
    entries = _read_entries(root / "stats.jsonl")
    window = entries[-WINDOW:]
    summary = _summarize(window)

    rows = "\n".join(_row_html(e) for e in reversed(window))

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
.summary{{display:flex;gap:1em;flex-wrap:wrap;margin:1em 0;}}
.card{{background:#f6f6f6;padding:.5em 1em;border-radius:4px;min-width:8em;}}
.k{{color:#666;font-size:.8em;display:block;}}
.v{{font-weight:600;font-size:1.2em;}}
a{{color:#0366d6;}}
footer{{margin-top:2em;font-size:.85em;color:#666;}}
</style></head>
<body>
<h1>Pipeline stats — oops-all-vibes</h1>
<p>Rolling window: last {summary["runs_total"]} runs (max {WINDOW}).</p>
<div class="summary">
  <div class="card"><span class="k">runs</span><span class="v">{summary["runs_total"]}</span></div>
  <div class="card"><span class="k">first-try success</span><span class="v">{summary["first_try_success_pct"]}%</span></div>
  <div class="card"><span class="k">committed</span><span class="v">{summary["overall_commit_pct"]}%</span></div>
  <div class="card"><span class="k">avg duration</span><span class="v">{summary["avg_duration_s"]}s</span></div>
</div>
<table>
  <thead><tr><th>date</th><th>attempts</th><th>committed</th><th>failures</th><th>api errors</th><th>duration (s)</th></tr></thead>
  <tbody>
{rows}
  </tbody>
</table>
<footer><a href="/">Back to today</a></footer>
</body></html>
"""
    (root / "stats.html").write_text(html)
