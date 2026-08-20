"""Republish an archived day from a model_ab run's output.

Takes the raw <site>/<log> a model_ab arm produced for a date and installs it
as that date's edition, going through finalize_html so the page gets the same
link normalization and analytics injection as anything the daily pipeline
ships. Copying the A/B files by hand would skip that and publish an untracked
page with unrewritten links.

The prompt is left alone: model_ab replayed prompts/<date>.md verbatim, so the
prompt on disk is already the one that produced this output.

Dry run by default — pass --write to actually touch the working tree, then
commit yourself.

    python -m scripts.republish_from_ab --date 2026-08-19 --arm opus
    python -m scripts.republish_from_ab --date 2026-08-19 --arm opus --write
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import frontmatter

from scripts.build_archive_index import build_archive_index
from scripts.validate_output import validate_output
from scripts.verify_archive_claims import hard_failures, verify_archive_claims
from scripts.write_outputs import finalize_html


REPO_ROOT = Path(__file__).resolve().parent.parent


def latest_archived_date(repo_root: Path) -> str | None:
    """The most recent day in the archive, which is what index.html mirrors."""
    dates = sorted(
        p.stem for p in (repo_root / "archive").glob("*.html") if p.name != "index.html"
    )
    return dates[-1] if dates else None


def analytics_is_configured(repo_root: Path) -> bool:
    """True unless this repo ships an analytics tag we'd be about to drop.

    inject_tech reads GOATCOUNTER_CODE from the environment. Locally that is
    usually unset (it's a GitHub Actions variable), so finalize_html quietly
    produces a page with no tracking. Shipping that looks fine and records
    nothing, which is the exact failure this tool exists to prevent.
    """
    if os.environ.get("GOATCOUNTER_CODE"):
        return True
    shipped = sorted(
        p for p in (repo_root / "archive").glob("*.html") if p.name != "index.html"
    )
    if not shipped:
        return True  # nothing to compare against; assume analytics aren't in use
    return "goatcounter" not in shipped[-1].read_text().lower()


def republish(
    date_str: str,
    arm: str,
    repo_root: Path,
    ab_dir: Path,
    *,
    write: bool = False,
    html_path: Path | None = None,
    diary_path: Path | None = None,
    allow_no_analytics: bool = False,
) -> int:
    html_path = html_path or ab_dir / date_str / f"{arm}.html"
    diary_path = diary_path or ab_dir / date_str / f"{arm}.diary.md"
    prompt_path = repo_root / "prompts" / f"{date_str}.md"

    for path in (html_path, diary_path, prompt_path):
        if not path.is_file():
            print(f"republish: missing {path}", file=sys.stderr)
            return 1

    html = html_path.read_text()
    diary = diary_path.read_text()

    # The diary's own frontmatter date must match, or Georgia's history ends up
    # filed under the wrong day.
    meta_date = str(frontmatter.loads(diary).metadata.get("date", ""))
    if meta_date != date_str:
        print(
            f"republish: diary frontmatter says date {meta_date!r}, expected {date_str!r}",
            file=sys.stderr,
        )
        return 1

    if not allow_no_analytics and not analytics_is_configured(repo_root):
        print(
            "republish: GOATCOUNTER_CODE is not set, but the last shipped page carries\n"
            "  an analytics tag. Publishing now would drop it and the page would record\n"
            "  no traffic. Re-run with the code set, e.g.:\n"
            "      GOATCOUNTER_CODE=clarkle python -m scripts.republish_from_ab ...\n"
            "  (the code is not a secret — it appears in every shipped page)\n"
            "  Pass --allow-no-analytics to publish without it anyway.",
            file=sys.stderr,
        )
        return 1

    facts = json.loads((repo_root / "facts.json").read_text())
    valid, failures = validate_output(html, diary, facts, date_str)
    if not valid:
        print("republish: this output would not have passed the daily gate:", file=sys.stderr)
        for reason in failures:
            print(f"  - {reason}", file=sys.stderr)
        return 1

    finalized = finalize_html(html, date_str, repo_root)

    # Check the finished page, not the raw one — that is what readers get.
    discrepancies = verify_archive_claims(finalized, repo_root, date_str)
    hard = hard_failures(discrepancies)
    for d in discrepancies:
        stream = sys.stderr if d in hard else sys.stdout
        print(f"republish: archive {d.severity}: {d.message}", file=stream)
    if hard:
        print("republish: refusing to publish over hard archive failures", file=sys.stderr)
        return 1

    latest = latest_archived_date(repo_root)
    is_tip = latest == date_str

    targets = [
        (repo_root / "archive" / f"{date_str}.html", finalized),
        (repo_root / "log" / f"{date_str}.md", diary),
    ]
    # index.html mirrors the newest edition. Replacing an older day must not
    # drag the front page backwards.
    if is_tip:
        targets.append((repo_root / "index.html", finalized))

    print()
    print(f"republish: {date_str} from {arm} ({len(finalized.encode())} bytes finalized)")
    print(f"republish: latest archived day is {latest} — "
          f"{'this IS the tip, index.html will be updated' if is_tip else 'older day, index.html left alone'}")
    for path, _ in targets:
        print(f"  would write {path.relative_to(repo_root)}")
    print(f"  prompts/{date_str}.md unchanged (model_ab replayed it verbatim)")

    if not write:
        print("\nrepublish: dry run — nothing written. Pass --write to apply.")
        return 0

    for path, content in targets:
        path.write_text(content)
    build_archive_index(repo_root)
    print("\nrepublish: written. Review with `git diff --stat`, then commit.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--date", required=True, help="the day to republish, YYYY-MM-DD")
    parser.add_argument("--arm", default="opus", help="which model_ab arm to take (default opus)")
    parser.add_argument("--ab-dir", type=Path, default=REPO_ROOT / "model-ab",
                        help="model_ab output directory")
    parser.add_argument("--write", action="store_true",
                        help="actually write the files (default is a dry run)")
    parser.add_argument("--html", type=Path,
                        help="page to publish, instead of <ab-dir>/<date>/<arm>.html")
    parser.add_argument("--diary", type=Path,
                        help="diary to publish, instead of <ab-dir>/<date>/<arm>.diary.md")
    parser.add_argument("--allow-no-analytics", action="store_true",
                        help="publish even though GOATCOUNTER_CODE is unset")
    args = parser.parse_args(argv)
    return republish(
        args.date,
        args.arm,
        REPO_ROOT,
        args.ab_dir.expanduser(),
        write=args.write,
        html_path=args.html.expanduser() if args.html else None,
        diary_path=args.diary.expanduser() if args.diary else None,
        allow_no_analytics=args.allow_no_analytics,
    )


if __name__ == "__main__":
    sys.exit(main())
