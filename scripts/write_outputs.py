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


REPO_ROOT = Path(__file__).resolve().parent.parent

_TRUTHY = {"1", "true", "yes", "on"}


def _maybe_inject_tech(html: str, date_str: str) -> str:
    """Call scripts.inject_tech if it exists (story_010). No-op otherwise."""
    try:
        from scripts.inject_tech import inject_tech  # type: ignore
    except ImportError:
        return html
    return inject_tech(html, date_str, os.environ.get("GOATCOUNTER_CODE"))


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

    # Injection (after validation, before writing)
    html = _maybe_inject_tech(html, date_str)

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
