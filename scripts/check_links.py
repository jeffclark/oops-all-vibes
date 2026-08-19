"""Check that every date-shaped link in the site resolves to a real file.

The regression guard for normalize_links. Exits non-zero and prints the
offenders if anything on the site points at a snapshot that isn't there.
"""
from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.normalize_links import archive_date, canonical_href


REPO_ROOT = Path(__file__).resolve().parent.parent


def site_pages(root: Path) -> list[Path]:
    """Every HTML page a visitor can land on, index first."""
    pages = [root / "index.html"]
    pages.extend(sorted((root / "archive").glob("*.html")))
    return [p for p in pages if p.exists()]


def broken_links(root: Path | None = None) -> list[tuple[Path, str, str]]:
    """Return (page, href, reason) for every date link that won't resolve."""
    root = root or REPO_ROOT
    available = {
        p.stem for p in (root / "archive").glob("*.html") if p.name != "index.html"
    }
    problems: list[tuple[Path, str, str]] = []

    for page in site_pages(root):
        soup = BeautifulSoup(page.read_text(), "html.parser")
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            date_str = archive_date(href)
            if date_str is None:
                continue
            if date_str not in available:
                problems.append((page, href, f"no snapshot for {date_str}"))
            elif href.partition("#")[0] != canonical_href(date_str):
                problems.append((page, href, f"not canonical; want {canonical_href(date_str)}"))

    return problems


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else REPO_ROOT
    problems = broken_links(root)
    if not problems:
        print(f"check_links: {len(site_pages(root))} pages, all date links resolve")
        return 0

    for page, href, reason in problems:
        print(f"{page.relative_to(root)}: {href} — {reason}", file=sys.stderr)
    print(f"check_links: {len(problems)} broken date link(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
