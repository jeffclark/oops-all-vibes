"""Daily pipeline orchestrator.

Selects today's corpus frames, assembles Georgia's prompt, calls the model,
validates output, retries once on validation or missing-tag failure, and records
pipeline stats on every exit path. Exits 0 on success (files written +
committed); 1 on failure (no commit — yesterday's site stays live).

**Selection runs before assembly, not after.** The prompt depends on what was
selected: it needs the shown frame ids to feed her prior verdicts back, and it
needs to know whether any frame was shown at all to pick between the two corpus
sentinels. Reading that order backwards would mean retrofitting a public
signature halfway through.

**The corpus can never cost a day.** Three layers:

1. `selection_for_date` swallows everything — missing manifest, malformed JSON,
   a cap violation — and returns an empty selection.
2. A dead `file_id` is the failure the local checks cannot catch: it makes the
   Messages request fail *before* inference, so it arrives as an API error rather
   than a block-building error, and a plain retry would hit the same dead file.
   The first API error on a corpus-bearing call drops the corpus and goes once
   more, text-only.
3. Nothing about `<taste>` can fail a run; it only produces warnings.
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
from scripts.call_model import ModelOutputError, call_model
from scripts.corpus import select as corpus_select
from scripts.fetch_daily_inputs import (
    RetirementError,
    RosterError,
    load_roster,
    apply_retirement,
    retirement_from_diary,
)
from scripts.record_stats import record_stats
from scripts.validate_output import validate_output, validate_taste
from scripts.verify_archive_claims import (
    Discrepancy,
    SOFT,
    hard_failures,
    soft_failures,
    verify_archive_claims,
)
from scripts.write_outputs import finalize_html, write_outputs


MODEL_TAG_HINT = (
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



def _apply_declared_retirement(
    diary: str, today: str, repo_root: Path, *, no_commit: bool = False
) -> None:
    """Retire whatever Georgia named in her log frontmatter.

    Never fatal. A malformed or impossible choice leaves the roster alone, which
    means the countdown stays expired and she gets asked again tomorrow — the
    demand doesn't quietly disappear because the parse failed.

    A dry run must not touch it. Retirement is permanent and nothing else in the
    pipeline can undo it, so a --no-commit smoke test that quietly dropped a
    source would be the worst kind of surprise.
    """
    declared = retirement_from_diary(diary)
    if declared is None:
        return
    if no_commit:
        # Report what the real run would actually do, not just what she asked
        # for — the gates (cycle due, on the roster, not the last one) reject
        # most declarations, and a dry run that says "would have retired X"
        # regardless is worse than saying nothing.
        try:
            state = load_roster(repo_root / "inputs" / "roster.json")
            key = declared[0]
            every = int(state.get("retire_every_builds", 0))
            if key not in state.get("roster", []):
                verdict = f"would be REJECTED — {key!r} is not on the roster"
            elif len(state.get("roster", [])) <= 1:
                verdict = "would be REJECTED — refusing to empty the roster"
            elif int(state.get("builds_this_cycle", 0)) < every:
                verdict = (f"would be REJECTED — no retirement due "
                           f"(build {state.get('builds_this_cycle', 0)} of {every})")
            else:
                verdict = f"would retire {key!r}"
        except RosterError as exc:
            verdict = f"would be REJECTED — {exc}"
        print(f"run_georgia: dry run — {verdict}; roster untouched", file=sys.stderr)
        return
    key, reason = declared
    try:
        state = apply_retirement(
            key, reason, date.fromisoformat(today), repo_root / "inputs" / "roster.json"
        )
    except (RetirementError, RosterError) as exc:
        print(f"run_georgia: retirement rejected — {exc}", file=sys.stderr)
        return
    print(
        f"run_georgia: Georgia retired '{key}'. Roster is now "
        f"{state['roster']} — a replacement fetcher is owed.",
        file=sys.stderr,
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
    corpus_warnings: list[str] = []
    # Tokens for the response that shipped. A run that retried spent more than
    # this; the shipped figure is what MAX_TOKENS actually had to accommodate.
    input_tokens = 0
    output_tokens = 0

    run_date = date.fromisoformat(today)
    selection = corpus_select.selection_for_date(
        run_date, repo_root / "corpus" / "manifest.json"
    )
    prompt = assemble_prompt(
        run_date, repo_root=repo_root, shown_frame_ids=list(selection.frame_ids)
    )
    request = corpus_select.build_content(selection, prompt)
    corpus_dropped = False

    attempt = 0
    while attempt < 2:
        attempt += 1
        attempts = attempt
        try:
            result = call_model(request)
            html, diary = result.html, result.diary
            input_tokens = result.input_tokens
            output_tokens = result.output_tokens
        except APIError as exc:
            api_errors += 1
            print(f"run_georgia: API error on attempt {attempt}: {exc}", file=sys.stderr)
            if selection.shown and not corpus_dropped:
                # Most likely a frame was deleted out from under the manifest, which
                # fails the request before inference — the same request would fail
                # again. Drop the corpus and rebuild the prompt so she is told the
                # shelf is dark and is not asked for verdicts on frames she never
                # saw. This is not a validation failure, so it does not spend one of
                # the two validation attempts.
                corpus_dropped = True
                attempt -= 1
                corpus_warnings.append(f"corpus_dropped: {type(exc).__name__}: {exc}")
                print(
                    "run_georgia: retrying text-only without the corpus",
                    file=sys.stderr,
                )
                selection = corpus_select.EMPTY
                prompt = assemble_prompt(run_date, repo_root=repo_root, shown_frame_ids=[])
                if validation_failures:
                    # Reassembling from scratch is what makes the shelf read as dark,
                    # but it also drops any hint an earlier failed attempt earned.
                    # Those reasons are about the site and the diary and stay valid
                    # with the corpus gone, so put them back.
                    prompt = add_retry_hint(prompt, validation_failures[-1])
                request = prompt
                continue
            record_stats(
            today, attempts, validation_failures, api_errors, committed, start,
            repo_root=repo_root, corpus_warnings=corpus_warnings,
        )
            return 1
        except ModelOutputError as exc:
            reasons = [MODEL_TAG_HINT]
            validation_failures.append(reasons)
            if attempt == 1:
                print(
                    f"run_georgia: ModelOutputError on attempt 1; retrying with hint. "
                    f"Raw excerpt: {exc.raw[:200]!r}",
                    file=sys.stderr,
                )
                prompt = add_retry_hint(prompt, reasons)
                request = corpus_select.build_content(selection, prompt)
                continue
            print(
                f"run_georgia: ModelOutputError twice. Raw excerpt: {exc.raw[:500]!r}",
                file=sys.stderr,
            )
            record_stats(
            today, attempts, validation_failures, api_errors, committed, start,
            repo_root=repo_root, corpus_warnings=corpus_warnings,
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
                    request = corpus_select.build_content(selection, prompt)
                    continue
                print(
                    f"run_georgia: archive claims failed twice. Latest: {reasons}",
                    file=sys.stderr,
                )
                record_stats(
                    today, attempts, validation_failures, api_errors, committed, start,
                    repo_root=repo_root, corpus_warnings=corpus_warnings,
                )
                return 1

            # Soft findings — a miscount or a wrong importance marker — are
            # recorded and shipped. Not worth a day of no site.
            warnings = [d.message for d in soft_failures(found)]
            for warning in warnings:
                print(f"run_georgia: archive warning: {warning}", file=sys.stderr)

            # Her verdicts on today's frames. Never fatal: a missing or malformed
            # <taste> block is a warning and the day still ships. On a dark day we
            # never asked for one, so we do not complain about its absence either.
            taste_entries: list[dict] = []
            if selection.shown:
                taste_entries, taste_warnings = validate_taste(
                    result.taste, selection.frame_ids, today
                )
                corpus_warnings.extend(taste_warnings)
            for warning in corpus_warnings:
                print(f"run_georgia: corpus warning: {warning}", file=sys.stderr)

            # Georgia's retirement is binding: if she named one, the source
            # comes off the roster now. Done before write_outputs so the
            # updated roster rides the same `git add -A` commit.
            _apply_declared_retirement(diary, today, repo_root, no_commit=no_commit)

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
                corpus_warnings=corpus_warnings,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            write_outputs(
                today,
                final_html,
                diary,
                prompt,
                no_commit=no_commit,
                repo_root=repo_root,
                frame_ids=selection.frame_ids,
                manifest_version=selection.manifest_version,
                shape_show_ids=selection.shape_show_ids,
                taste_entries=taste_entries,
            )
            return 0

        validation_failures.append(reasons)
        if attempt == 1:
            print(
                f"run_georgia: validation failed on attempt 1: {reasons}",
                file=sys.stderr,
            )
            prompt = add_retry_hint(prompt, reasons)
            request = corpus_select.build_content(selection, prompt)
            continue

        print(f"run_georgia: validation failed twice. Latest reasons: {reasons}", file=sys.stderr)
        record_stats(
            today, attempts, validation_failures, api_errors, committed, start,
            repo_root=repo_root, corpus_warnings=corpus_warnings,
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
