"""Stub — replaced in story_011.

record_stats(date, attempts, validation_failures, api_errors, committed, start_time)
will append a JSON line to stats.jsonl and regenerate stats.html.
"""
from __future__ import annotations


def record_stats(
    date: str,
    attempts: int,
    validation_failures: list[list[str]],
    api_errors: int,
    committed: bool,
    start_time: float,
) -> None:
    # TODO(story_011): append to stats.jsonl and rebuild stats.html
    pass
