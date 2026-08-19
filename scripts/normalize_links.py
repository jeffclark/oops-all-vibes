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

# Every date-shaped path Georgia has emitted, and then some. She invents a
# new URL shape most days — one page shipped its whole archive list as
# /YYYY/MM/DD — so this matches the date liberally rather than enumerating
# the forms seen so far: either separator, optional zero-padding, optional
# archive/ prefix, optional .htm(l). Still anchored end-to-end, so the
# /log/<date>.md and /prompts/<date>.md links inject_tech adds cannot match.
_PATH_RE = re.compile(
    r"^(?:\./)?/?(?:archive/)?"
    r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"
    r"(?:\.html?)?/?$",
    re.IGNORECASE,
)


def canonical_href(date_str: str) -> str:
    """The one correct URL for a day's snapshot."""
    return f"/archive/{date_str}.html"


def internal_path(href: str) -> str | None:
    """The path an href points at within this site, or None if it leaves it.

    Handles absolute links to our own hosts, protocol-relative links, and
    root- or document-relative paths. Returns None for external hosts, other
    schemes (mailto:, tel:, javascript:), and bare fragments or queries,
    which never address a file.
    """
    href = href.strip()
    if not href or href.startswith(("#", "?")):
        return None

    scheme = re.match(r"^([a-z][a-z0-9+.-]*):", href, re.IGNORECASE)
    if scheme and scheme.group(1).lower() not in ("http", "https"):
        return None

    origin = _ORIGIN_RE.match(href)
    if origin:
        host = origin.group(1).split("@")[-1].split(":")[0].lower()
        if host not in SITE_HOSTS:
            return None
        href = href[origin.end():] or "/"
    elif scheme:
        # An http(s) scheme with no //authority isn't a path we serve.
        return None

    return href.split("#", 1)[0].split("?", 1)[0] or "/"


def archive_date(href: str) -> str | None:
    """Return the date an href is trying to reach, or None if it isn't one.

    Fragments are ignored for matching and re-attached by the caller.
    """
    path = internal_path(href)
    if path is None:
        return None
    match = _PATH_RE.match(path)
    if not match:
        return None
    year, month, day = match.groups()
    # Zero-pad so /2026-8-7 and /2026/08/07 land on the same snapshot.
    return f"{year}-{int(month):02d}-{int(day):02d}"


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
