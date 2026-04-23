"""Regenerate archive/index.html from the files in archive/.

Deliberately boring: this page is infrastructure, not art. Georgia does not
reimagine it. Keeps to a simple reverse-chron list of dated links.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def build_archive_index(repo_root: Path | None = None) -> None:
    root = repo_root or REPO_ROOT
    archive = root / "archive"
    archive.mkdir(parents=True, exist_ok=True)

    entries = sorted(
        (p for p in archive.glob("*.html") if p.name != "index.html"),
        key=lambda p: p.stem,
        reverse=True,
    )
    items = "\n".join(f'    <li><a href="./{p.name}">{p.stem}</a></li>' for p in entries)

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Archive — oops-all-vibes</title>
<style>body{{font-family:system-ui,sans-serif;max-width:40em;margin:2em auto;padding:0 1em;color:#222;}}li{{margin:.25em 0;}}a{{color:#0366d6;}}</style>
</head><body>
<h1>Archive — oops-all-vibes</h1>
<p>Every day's site, preserved. Click a date to see that day's Georgia.</p>
<ul>
{items}
</ul>
<p><a href="/">Back to today</a></p>
</body></html>
"""
    (archive / "index.html").write_text(html)
