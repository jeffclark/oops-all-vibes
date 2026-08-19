"""Check that every internal link in the site resolves to a real file.

The regression guard for normalize_links. Date links get the stricter test —
they must be in canonical form, not merely resolvable — because one URL shape
is what keeps them checkable. Everything else just has to exist.

Resolution is a plain identity mapping from URL path to file, which is what
the live site does: /log/<date>.md serves that file's bytes even though Jekyll
is in the loop and the file opens with YAML front matter. We deliberately
don't credit the extensionless fallback (/foo serving foo.html) — counting on
behavior we haven't confirmed is how a checker ends up green while the site
404s.
"""
from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.normalize_links import archive_date, canonical_href, internal_path


REPO_ROOT = Path(__file__).resolve().parent.parent


def site_pages(root: Path) -> list[Path]:
    """Every HTML page a visitor can land on, index first."""
    pages = [root / "index.html"]
    pages.extend(sorted((root / "archive").glob("*.html")))
    return [p for p in pages if p.exists()]


def resolve_internal(path: str, page: Path, root: Path) -> Path | None:
    """The file a site path maps to, or None if nothing is there.

    Relative paths resolve against the page's own directory, matching how a
    browser reads them.
    """
    if path.startswith("/"):
        base, rel = root, path.lstrip("/")
    else:
        base, rel = page.parent, path

    target = base / rel if rel else base
    try:
        resolved = target.resolve()
        root_resolved = root.resolve()
        if resolved != root_resolved and root_resolved not in resolved.parents:
            return None  # escaped the site with ../
    except (OSError, RuntimeError):
        return None

    if not rel or rel.endswith("/") or target.is_dir():
        target = target / "index.html"
    return target if target.is_file() else None


def broken_links(root: Path | None = None) -> list[tuple[Path, str, str]]:
    """Return (page, href, reason) for every internal link that won't work."""
    root = root or REPO_ROOT
    available = {
        p.stem for p in (root / "archive").glob("*.html") if p.name != "index.html"
    }
    problems: list[tuple[Path, str, str]] = []

    for page in site_pages(root):
        soup = BeautifulSoup(page.read_text(), "html.parser")
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            path = internal_path(href)
            if path is None:
                continue  # external, mailto:, or a bare fragment

            date_str = archive_date(href)
            if date_str is not None:
                if date_str not in available:
                    problems.append((page, href, f"no snapshot for {date_str}"))
                elif href.partition("#")[0] != canonical_href(date_str):
                    problems.append(
                        (page, href, f"not canonical; want {canonical_href(date_str)}")
                    )
            elif resolve_internal(path, page, root) is None:
                problems.append((page, href, "no file at that path"))

    return problems


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else REPO_ROOT
    problems = broken_links(root)
    if not problems:
        print(f"check_links: {len(site_pages(root))} pages, all internal links resolve")
        return 0

    for page, href, reason in problems:
        print(f"{page.relative_to(root)}: {href} — {reason}", file=sys.stderr)
    print(f"check_links: {len(problems)} broken internal link(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
