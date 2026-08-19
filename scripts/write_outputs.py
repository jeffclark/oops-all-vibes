"""Write Georgia's outputs to disk, rebuild the archive index, commit.

Pushes to origin only when GEORGIA_PUSH is set truthy (CI sets it; local runs
don't — honors the CLAUDE.md guidance to stay local until story_012).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


def write_outputs(
    date_str: str,
    html: str,
    diary: str,
    prompt: str,
    *,
    no_commit: bool = False,
    repo_root: Path | None = None,
) -> None:
    root = repo_root or REPO_ROOT

    # Rewriting + injection (after validation, before writing). run_georgia
    # finalizes ahead of its verification gate; this is a no-op on the result.
    html = finalize_html(html, date_str, root)

    # Write Georgia's outputs
    (root / "index.html").write_text(html)
    (root / "archive" / f"{date_str}.html").write_text(html)
    (root / "log" / f"{date_str}.md").write_text(diary)
    (root / "prompts" / f"{date_str}.md").write_text(prompt)

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
