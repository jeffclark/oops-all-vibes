"""Append one JSON-lines entry per run to stats.jsonl, then regenerate stats.html.

record_stats is called from run_georgia on every exit path (success AND
failure). Line schema:
    {"date": str, "attempts": int, "validation_failures": [str, ...],
     "api_errors": int, "committed": bool, "duration_ms": int,
     "archive_warnings": [str, ...]}

archive_warnings holds soft findings from verify_archive_claims — claims the
page makes about the archive that aren't true but weren't worth costing a
day of site over. Older lines predate the key; readers must tolerate it
being absent.
"""
from __future__ import annotations

import json
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
    }
    stats_file = root / "stats.jsonl"
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    with stats_file.open("a") as f:
        f.write(json.dumps(line) + "\n")
    build_stats_page(repo_root=root)
