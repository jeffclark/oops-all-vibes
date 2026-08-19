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

import frontmatter
from anthropic import APIError

from scripts.assemble_prompt import REPO_ROOT, assemble_prompt
from scripts.call_sonnet import SonnetOutputError, call_sonnet
from scripts.record_stats import record_stats
from scripts.validate_output import validate_output
from scripts.verify_archive_claims import (
    Discrepancy,
    SOFT,
    hard_failures,
    soft_failures,
    verify_archive_claims,
)
from scripts.write_outputs import finalize_html, write_outputs


SONNET_TAG_HINT = (
    "Your previous response didn't include the <site>...</site> or "
    "<log>...</log> tags correctly. Both are required."
)


def _diary_importance(diary: str) -> int | None:
    """Today's importance, for checking what the page claims about today."""
    try:
        value = frontmatter.loads(diary).metadata.get("importance")
    except Exception:  # noqa: BLE001 — validate_output already reports bad frontmatter
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def safe_verify(html: str, repo_root: Path, today: str, diary: str) -> list[Discrepancy]:
    """verify_archive_claims, but a bug in the checker can't cost a day.

    A failure to verify is reported as a soft finding rather than raised: the
    gate exists to stop the site lying about its own history, not to become a
    new way for the site to go dark.
    """
    try:
        return verify_archive_claims(html, repo_root, today, _diary_importance(diary))
    except Exception as exc:  # noqa: BLE001
        print(f"run_georgia: archive verification failed to run: {exc}", file=sys.stderr)
        return [Discrepancy(SOFT, f"Archive verification did not run ({exc}).")]


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
            record_stats(
            today, attempts, validation_failures, api_errors, committed, start,
            repo_root=repo_root,
        )
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
            record_stats(
            today, attempts, validation_failures, api_errors, committed, start,
            repo_root=repo_root,
        )
            return 1

        is_valid, reasons = validate_output(html, diary, facts, today)
        if is_valid:
            # Verify the page that will actually ship — links rewritten,
            # footer injected — before anything is written or committed.
            final_html = finalize_html(html, today, repo_root)
            found = safe_verify(final_html, repo_root, today, diary)
            hard = hard_failures(found)

            if hard:
                # The page is inventing archive days or linking to days that
                # don't exist. That's the site lying about its own record, so
                # it doesn't ship.
                reasons = [d.message for d in hard]
                validation_failures.append(reasons)
                if attempt == 1:
                    print(
                        f"run_georgia: archive claims failed on attempt 1: {reasons}",
                        file=sys.stderr,
                    )
                    prompt = add_retry_hint(prompt, reasons)
                    continue
                print(
                    f"run_georgia: archive claims failed twice. Latest: {reasons}",
                    file=sys.stderr,
                )
                record_stats(
                    today, attempts, validation_failures, api_errors, committed, start,
                    repo_root=repo_root,
                )
                return 1

            # Soft findings — a miscount or a wrong importance marker — are
            # recorded and shipped. Not worth a day of no site.
            warnings = [d.message for d in soft_failures(found)]
            for warning in warnings:
                print(f"run_georgia: archive warning: {warning}", file=sys.stderr)

            # Record stats BEFORE write_outputs so this run's stats line is
            # included in write_outputs's `git add -A` commit.
            committed = True
            record_stats(
                today,
                attempts,
                validation_failures,
                api_errors,
                committed,
                start,
                repo_root=repo_root,
                archive_warnings=warnings,
            )
            write_outputs(
                today, final_html, diary, prompt, no_commit=no_commit, repo_root=repo_root
            )
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
        record_stats(
            today, attempts, validation_failures, api_errors, committed, start,
            repo_root=repo_root,
        )
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
