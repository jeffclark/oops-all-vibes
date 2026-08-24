"""Append one JSON-lines entry per run to stats.jsonl, then regenerate stats.html.

record_stats is called from run_georgia on every exit path (success AND
failure). Line schema:
    {"date": str, "attempts": int, "validation_failures": [str, ...],
     "api_errors": int, "committed": bool, "duration_ms": int,
     "archive_warnings": [str, ...], "corpus_warnings": [str, ...],
     "input_tokens": int, "output_tokens": int}

archive_warnings holds soft findings from verify_archive_claims — claims the
page makes about the archive that aren't true but weren't worth costing a
day of site over. Older lines predate the key; readers must tolerate it
being absent.

corpus_warnings holds everything the corpus reported without stopping the day:
`corpus_dropped` when a corpus-bearing call failed and the run went text-only,
plus any <taste> lines that were skipped or dropped. Kept separate from
archive_warnings so the two stay distinguishable — one is the page lying about
its own history, the other is the shelf misbehaving.

input_tokens/output_tokens describe the response that shipped, so a run that
retried spent more than the line records. output_tokens is the one to watch:
it includes adaptive thinking and it is what MAX_TOKENS caps, so it shows the
headroom shrinking as the prompt grows. 0 means no response shipped — a
failed run — not a free one. Older lines predate both keys.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from scripts.build_stats_page import build_stats_page


REPO_ROOT = Path(__file__).resolve().parent.parent


def record_stats(
    date: str,
    attempts: int,
    validation_failures: list[list[str]],
    api_errors: int,
    committed: bool,
    start_time: float,
    *,
    repo_root: Path | None = None,
    archive_warnings: list[str] | None = None,
    corpus_warnings: list[str] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    root = repo_root or REPO_ROOT
    duration_ms = int((time.monotonic() - start_time) * 1000)
    flat = [reason for attempt_reasons in validation_failures for reason in attempt_reasons]
    line = {
        "date": date,
        "attempts": attempts,
        "validation_failures": flat,
        "api_errors": api_errors,
        "committed": committed,
        "duration_ms": duration_ms,
        "archive_warnings": archive_warnings or [],
        "corpus_warnings": corpus_warnings or [],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    stats_file = root / "stats.jsonl"
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    with stats_file.open("a") as f:
        f.write(json.dumps(line) + "\n")
    _safe_build_stats_page(root)


def _safe_build_stats_page(root: Path) -> None:
    """Rebuild stats.html, but a bad input file can never cost a day of site.

    record_stats runs on every exit path, including the success path *after*
    run_georgia has recorded committed=True and before write_outputs runs. Since
    the corpus stories the stats page now reads two corpus-owned files
    (corpus/verify.json, corpus/consistency.jsonl), a malformed one of those would
    otherwise be a way for a corpus problem to take the site down — the exact
    thing the corpus is not allowed to do. Same reasoning as
    run_georgia.safe_verify and write_outputs._safe_normalize_links.
    """
    try:
        build_stats_page(repo_root=root)
    except Exception as exc:  # noqa: BLE001 — a stats page must never cost a day
        print(f"record_stats: could not rebuild stats.html ({exc})", file=sys.stderr)
