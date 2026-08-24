"""Write Georgia's outputs to disk, rebuild the archive index, commit.

Pushes to origin only when GEORGIA_PUSH is set truthy (CI sets it; local runs
don't — honors the CLAUDE.md guidance to stay local until story_012).

Two corpus-era additions, both append-only:

- `prompts/<date>.md` gains a `## Corpus shown` section when frames were sent.
  That file claims to be the day's prompt; once part of the prompt is images, it
  is a lie unless it says which ones. The archive being honest is a standing
  property in this repo, not a nicety.
- `corpus/preferences.jsonl` gains today's verdicts. Never rewritten, never
  reordered — the record stands, and she is free to contradict it tomorrow.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from scripts.build_archive_index import build_archive_index
from scripts.normalize_links import normalize_links


REPO_ROOT = Path(__file__).resolve().parent.parent

_TRUTHY = {"1", "true", "yes", "on"}


def _available_dates(root: Path, date_str: str) -> set[str]:
    """Dates that have a snapshot, from the caller's point of view.

    date_str is unioned in because archive/<today>.html is written further
    down this same module — without it, Georgia's link to today would be
    de-linked as a dead date.
    """
    archive = root / "archive"
    dates = {p.stem for p in archive.glob("*.html") if p.name != "index.html"}
    dates.add(date_str)
    return dates


def _safe_normalize_links(html: str, available_dates: set[str]) -> str:
    """normalize_links, but never fatal.

    run_georgia records stats with committed=True *before* calling
    write_outputs, so anything that raises in here means no site ships while
    the stats line claims one did. A link we failed to fix is much cheaper
    than a day with no site, so degrade to a no-op.
    """
    try:
        return normalize_links(html, available_dates)
    except Exception as exc:  # noqa: BLE001 — a bad link must never cost a day
        print(f"write_outputs: normalize_links failed ({exc}); using raw HTML", file=sys.stderr)
        return html


def _maybe_inject_tech(html: str, date_str: str) -> str:
    """Call scripts.inject_tech if it exists (story_010). No-op otherwise."""
    try:
        from scripts.inject_tech import inject_tech  # type: ignore
    except ImportError:
        return html
    return inject_tech(html, date_str, os.environ.get("GOATCOUNTER_CODE"))


FINALIZED_MARKER = "<!--georgia:finalized-->"


def finalize_html(html: str, date_str: str, repo_root: Path | None = None) -> str:
    """Turn Georgia's raw output into the page that actually ships.

    Split out of write_outputs so run_georgia can verify the finished page —
    the one with rewritten links and the injected footer — before anything is
    written or committed.

    Finalizing twice would append a second footer, so the result is stamped
    and a second call returns it untouched. That stamp is why write_outputs
    can just call this unconditionally: no flag to pass, so no flag to pass
    wrongly.
    """
    if FINALIZED_MARKER in html:
        return html
    root = repo_root or REPO_ROOT
    html = _safe_normalize_links(html, _available_dates(root, date_str))
    return _maybe_inject_tech(html, date_str) + FINALIZED_MARKER


def append_preferences(
    entries: Sequence[dict[str, Any]], repo_root: Path
) -> None:
    """Append today's verdicts to corpus/preferences.jsonl. Never fatal.

    The accumulated file *is* the taste, so losing a day of it matters — but not
    as much as shipping the site does. A write failure here is reported and
    swallowed, exactly like normalize_links: run_georgia has already recorded
    committed=True by the time this runs.
    """
    if not entries:
        return
    path = repo_root / "corpus" / "preferences.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
    except OSError as exc:  # noqa: BLE001 — a preference line must never cost a day
        print(f"write_outputs: could not append preferences ({exc})", file=sys.stderr)


def prompt_archive_text(
    prompt: str,
    frame_ids: Sequence[str] | None,
    manifest_version: Any = None,
    shape_show_ids: Sequence[str] | None = None,
) -> str:
    """The prompt as archived: the text, plus what was shown alongside it.

    Omitted entirely when no frame was sent, so a text-only day archives exactly
    the bytes that were sent and nothing extra.
    """
    if not frame_ids:
        return prompt
    lines = [
        prompt.rstrip("\n"),
        "",
        "## Corpus shown",
        "",
        "The prompt above is the text half of the request. These frames were sent",
        "as images before it, referenced by Files API id, in this order:",
        "",
    ]
    lines += [f"- {fid}" for fid in frame_ids]
    if shape_show_ids:
        lines += ["", "Show-shape plots: " + ", ".join(shape_show_ids)]
    lines += ["", f"Manifest version: {manifest_version}", ""]
    return "\n".join(lines)


def write_outputs(
    date_str: str,
    html: str,
    diary: str,
    prompt: str,
    *,
    no_commit: bool = False,
    repo_root: Path | None = None,
    frame_ids: Sequence[str] | None = None,
    manifest_version: Any = None,
    shape_show_ids: Sequence[str] | None = None,
    taste_entries: Sequence[dict[str, Any]] | None = None,
) -> None:
    root = repo_root or REPO_ROOT

    # Rewriting + injection (after validation, before writing). run_georgia
    # finalizes ahead of its verification gate; this is a no-op on the result.
    html = finalize_html(html, date_str, root)

    # Write Georgia's outputs
    (root / "index.html").write_text(html)
    (root / "archive" / f"{date_str}.html").write_text(html)
    (root / "log" / f"{date_str}.md").write_text(diary)
    (root / "prompts" / f"{date_str}.md").write_text(
        prompt_archive_text(prompt, frame_ids, manifest_version, shape_show_ids)
    )

    # Her verdicts on what she saw. Written before the commit so they land in the
    # same `git add -A` as everything else the day produced.
    append_preferences(taste_entries or [], root)

    # Boring archive index
    build_archive_index(root)

    if no_commit:
        return

    _git_commit(date_str, root)
    if os.environ.get("GEORGIA_PUSH", "").lower() in _TRUTHY:
        _git_push(root)
    else:
        print(
            "write_outputs: GEORGIA_PUSH not set; committed locally without pushing",
            file=sys.stderr,
        )


def _git_commit(date_str: str, cwd: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"Georgia, {date_str}"],
        cwd=cwd,
        check=True,
    )


def _git_push(cwd: Path) -> None:
    result = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=cwd,
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        print(f"write_outputs: git push failed: {stderr}", file=sys.stderr)
        raise RuntimeError("git push failed")
