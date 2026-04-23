"""Daily pipeline orchestrator.

Assembles Georgia's prompt, calls Sonnet, validates output, retries once on
validation or missing-tag failure, and records pipeline stats on every exit
path. Exits 0 on success (files written + committed); 1 on failure (no commit
— yesterday's site stays live).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from anthropic import APIError

from scripts.assemble_prompt import REPO_ROOT, assemble_prompt
from scripts.call_sonnet import SonnetOutputError, call_sonnet
from scripts.record_stats import record_stats
from scripts.validate_output import validate_output
from scripts.write_outputs import write_outputs


SONNET_TAG_HINT = (
    "Your previous response didn't include the <site>...</site> or "
    "<log>...</log> tags correctly. Both are required."
)


def add_retry_hint(prompt: str, reasons: list[str]) -> str:
    bullets = "\n".join(f"- {r}" for r in reasons)
    return prompt + (
        "\n\n[validation-failure]\n"
        "Your previous attempt failed these checks:\n"
        f"{bullets}\n\n"
        "Try again. Fix these issues. Note the mishap somewhere in your diary "
        "entry for today — own it.\n"
        "[/validation-failure]\n"
    )


def run(today: str, facts: dict, repo_root: Path, *, no_commit: bool = False) -> int:
    start = time.monotonic()
    attempts = 0
    validation_failures: list[list[str]] = []
    api_errors = 0
    committed = False

    prompt = assemble_prompt(date.fromisoformat(today), repo_root=repo_root)

    for attempt in (1, 2):
        attempts = attempt
        try:
            html, diary = call_sonnet(prompt)
        except APIError as exc:
            api_errors += 1
            print(f"run_georgia: API error on attempt {attempt}: {exc}", file=sys.stderr)
            record_stats(today, attempts, validation_failures, api_errors, committed, start)
            return 1
        except SonnetOutputError as exc:
            reasons = [SONNET_TAG_HINT]
            validation_failures.append(reasons)
            if attempt == 1:
                print(
                    f"run_georgia: SonnetOutputError on attempt 1; retrying with hint. "
                    f"Raw excerpt: {exc.raw[:200]!r}",
                    file=sys.stderr,
                )
                prompt = add_retry_hint(prompt, reasons)
                continue
            print(
                f"run_georgia: SonnetOutputError twice. Raw excerpt: {exc.raw[:500]!r}",
                file=sys.stderr,
            )
            record_stats(today, attempts, validation_failures, api_errors, committed, start)
            return 1

        is_valid, reasons = validate_output(html, diary, facts, today)
        if is_valid:
            write_outputs(today, html, diary, prompt, no_commit=no_commit)
            committed = True
            record_stats(today, attempts, validation_failures, api_errors, committed, start)
            return 0

        validation_failures.append(reasons)
        if attempt == 1:
            print(
                f"run_georgia: validation failed on attempt 1: {reasons}",
                file=sys.stderr,
            )
            prompt = add_retry_hint(prompt, reasons)
            continue

        print(f"run_georgia: validation failed twice. Latest reasons: {reasons}", file=sys.stderr)
        record_stats(today, attempts, validation_failures, api_errors, committed, start)
        return 1

    # Unreachable — the loop always returns.
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Georgia's daily pipeline.")
    parser.add_argument(
        "--date",
        dest="run_date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Run date (YYYY-MM-DD). Defaults to today in UTC.",
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="Write files but skip git commit and push. Useful for local smoke tests.",
    )
    args = parser.parse_args(argv)

    today_date = args.run_date or datetime.now(timezone.utc).date()
    today = today_date.isoformat()
    facts = json.loads((REPO_ROOT / "facts.json").read_text())
    return run(today=today, facts=facts, repo_root=REPO_ROOT, no_commit=args.no_commit)


if __name__ == "__main__":
    raise SystemExit(main())
