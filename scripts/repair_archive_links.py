"""One-time repair of the date links in already-shipped pages.

117 snapshots went out with links Georgia invented — mostly `/YYYY-MM-DD`,
which 404s. This rewrites them in place using the same normalize_links core
the daily pipeline now uses, so old pages link the same way new ones do.

Idempotent: run it as often as you like. Only href values change; every
snapshot is already a BeautifulSoup fixed point, so re-serializing them
doesn't disturb the markup.

archive/index.html is skipped — build_archive_index regenerates it.
"""
from __future__ import annotations

import sys
from pathlib import Path

from scripts.check_links import site_pages
from scripts.normalize_links import normalize_links


REPO_ROOT = Path(__file__).resolve().parent.parent


def repair(root: Path | None = None) -> list[Path]:
    """Rewrite date links across the site. Returns the files that changed.

    Every page is normalized against the *current* set of snapshots, not the
    set that existed the day it shipped: the goal is links that work now, and
    a uniform set is also what keeps index.html byte-identical to the snapshot
    it was copied from.
    """
    root = root or REPO_ROOT
    available = {
        p.stem for p in (root / "archive").glob("*.html") if p.name != "index.html"
    }

    changed: list[Path] = []
    for page in site_pages(root):
        if page.name == "index.html" and page.parent.name == "archive":
            continue
        before = page.read_text()
        after = normalize_links(before, available)
        if after != before:
            page.write_text(after)
            changed.append(page)
    return changed


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else REPO_ROOT
    changed = repair(root)
    for page in changed:
        print(page.relative_to(root))
    print(f"repair_archive_links: rewrote {len(changed)} file(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
