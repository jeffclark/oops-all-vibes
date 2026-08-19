"""Rewrite Georgia's date links so they point at real archived files.

Georgia authors the archive list free-form, and she invents a different URL
shape most days (`/2026-08-17`, `/archive/2026-08-17`, an absolute link to a
subdomain that doesn't exist). Snapshots actually live at
`archive/YYYY-MM-DD.html`, so this canonicalizes every date-shaped link to
`/archive/<date>.html` — root-relative, because index.html and
archive/<date>.html are written byte-identical and the same href has to
resolve from both.

A link to a date with no snapshot becomes a <span>, keeping her text and
styling but dropping the dead click target. Nothing on the page is ever a
link that doesn't land somewhere.

Runs after validation (so validate_output still sees her raw output) and
before inject_tech. normalize_links never raises — see write_outputs.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

# Hosts that mean "this site". Anything else is somebody else's link.
SITE_HOSTS = frozenset({"clarkle.com", "www.clarkle.com", "jeff.clarkle.com"})

_ORIGIN_RE = re.compile(r"^(?:https?:)?//([^/]+)", re.IGNORECASE)

# The date-shaped paths Georgia has actually emitted, plus the canonical one.
# Anchored end-to-end so /log/<date>.md and /prompts/<date>.md — the links
# inject_tech adds — cannot match.
_PATH_RE = re.compile(
    r"^(?:\./)?(?:/)?(?:archive/)?(\d{4}-\d{2}-\d{2})(?:\.html)?/?$",
    re.IGNORECASE,
)


def canonical_href(date_str: str) -> str:
    """The one correct URL for a day's snapshot."""
    return f"/archive/{date_str}.html"


def archive_date(href: str) -> str | None:
    """Return the date an href is trying to reach, or None if it isn't one.

    Handles absolute links to our own hosts, protocol-relative links, and
    root- or document-relative paths. Fragments are ignored for matching and
    re-attached by the caller. Returns None for external hosts, mailto:,
    bare fragments, and every non-date path.
    """
    href = href.strip()
    if not href:
        return None

    # Bare fragment or query — never an archive link.
    if href.startswith(("#", "?")):
        return None

    # Reject any scheme we don't serve (mailto:, tel:, javascript:, ftp:).
    scheme = re.match(r"^([a-z][a-z0-9+.-]*):", href, re.IGNORECASE)
    if scheme and scheme.group(1).lower() not in ("http", "https"):
        return None

    origin = _ORIGIN_RE.match(href)
    if origin:
        host = origin.group(1).split("@")[-1].split(":")[0].lower()
        if host not in SITE_HOSTS:
            return None
        href = href[origin.end():] or "/"

    path = href.split("#", 1)[0].split("?", 1)[0]
    match = _PATH_RE.match(path)
    return match.group(1) if match else None


# Attributes that only make sense on a link. Everything else survives the
# swap to a span — a whitelist here silently ate data-archive-date, which is
# what verify_archive_claims blocks on, so de-linking an invented day also
# destroyed the evidence that the day was invented.
_LINK_ONLY_ATTRS = frozenset(
    {"href", "target", "rel", "download", "ping", "hreflang", "type", "referrerpolicy"}
)


def _to_span(soup: BeautifulSoup, tag) -> None:
    """Replace a dead link with a span, preserving everything but the link.

    Not unwrap(): these anchors carry class and inline style (including
    display:block) that the surrounding design depends on, plus ids that
    in-page anchors may point at.
    """
    span = soup.new_tag("span")
    for attr, value in tag.attrs.items():
        if attr.lower() not in _LINK_ONLY_ATTRS:
            span[attr] = value
    span.extend(tag.contents)
    tag.replace_with(span)


def normalize_links(html: str, available_dates: set[str]) -> str:
    """Point every date link at a snapshot that exists, or de-link it.

    available_dates must contain today's date even though its file hasn't
    been written yet, or Georgia's link to today gets de-linked.
    """
    soup = BeautifulSoup(html, "html.parser")
    changed = False

    for tag in soup.find_all("a", href=True):
        date_str = archive_date(tag["href"])
        if date_str is None:
            continue

        if date_str in available_dates:
            fragment = tag["href"].partition("#")[2]
            href = canonical_href(date_str)
            if fragment:
                href = f"{href}#{fragment}"
            if tag["href"] != href:
                tag["href"] = href
                changed = True
        else:
            _to_span(soup, tag)
            changed = True

    # Don't pay a serialization round-trip when there was nothing to fix.
    return str(soup) if changed else html
