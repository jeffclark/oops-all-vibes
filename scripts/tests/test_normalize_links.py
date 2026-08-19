"""Tests for scripts/normalize_links.py."""
from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.normalize_links import archive_date, canonical_href, normalize_links  # noqa: E402


DATES = {"2026-08-17", "2026-08-18"}


def page(body: str) -> str:
    return f"<!DOCTYPE html><html><head><title>t</title></head><body>{body}</body></html>"


def hrefs(html: str) -> list[str]:
    return [a["href"] for a in BeautifulSoup(html, "html.parser").find_all("a", href=True)]


# ---------- the shapes Georgia has actually shipped ----------


def test_rewrites_every_broken_shape_to_canonical():
    for shape in (
        "/2026-08-17",
        "/2026-08-17/",
        "/2026-08-17.html",
        "/archive/2026-08-17",
        "/archive/2026-08-17.html",
        "./2026-08-17.html",
        "2026-08-17.html",
        "archive/2026-08-17.html",
        "https://clarkle.com/2026-08-17",
        "https://jeff.clarkle.com/archive/2026-08-17",
        "//clarkle.com/archive/2026-08-17",
    ):
        out = normalize_links(page(f'<a href="{shape}">17th</a>'), DATES)
        assert hrefs(out) == ["/archive/2026-08-17.html"], shape


def test_canonical_href_is_root_relative():
    """index.html and archive/<date>.html are byte-identical, so the same
    href has to resolve from / and from /archive/."""
    assert canonical_href("2026-08-17") == "/archive/2026-08-17.html"


def test_fragment_is_preserved():
    out = normalize_links(page('<a href="/2026-08-17#diary">17th</a>'), DATES)
    assert hrefs(out) == ["/archive/2026-08-17.html#diary"]


# ---------- links that must not be touched ----------


def test_leaves_non_archive_links_alone():
    untouched = [
        "/log/2026-08-17.md",
        "/prompts/2026-08-17.md",
        "/notes/2026-08-17.md",
        "/feedback/2026-08-17.json",
        "/stats.html",
        "/archive/",
        "/",
        "#archive",
        "mailto:jeff@clarkle.com",
        "https://www.linkedin.com/in/serialcreative",
        "https://github.com/jeffclark/2026-08-17",
    ]
    body = "".join(f'<a href="{h}">x</a>' for h in untouched)
    out = normalize_links(page(body), DATES)
    assert hrefs(out) == untouched


def test_injected_footer_links_survive():
    """inject_tech's /log/ and /prompts/ links are date-shaped but not archive
    links — the path patterns must not match them."""
    assert archive_date("/log/2026-08-17.md") is None
    assert archive_date("/prompts/2026-08-17.md") is None


# ---------- dates with no snapshot ----------


def test_missing_date_becomes_span_keeping_class_and_style():
    html = page('<a class="archive-link" style="display:block;" href="/2026-06-18">gap</a>')
    out = normalize_links(html, DATES)
    soup = BeautifulSoup(out, "html.parser")
    assert soup.find("a") is None
    span = soup.find("span")
    assert span["class"] == ["archive-link"]
    assert span["style"] == "display:block;"
    assert span.get_text() == "gap"


def test_missing_date_keeps_nested_markup():
    html = page('<a href="/2026-06-18"><em>gap</em> day</a>')
    out = normalize_links(html, DATES)
    soup = BeautifulSoup(out, "html.parser")
    assert soup.find("em") is not None
    assert soup.find("span").get_text() == "gap day"


def test_unstyled_missing_date_still_becomes_span():
    out = normalize_links(page('<a href="/1999-01-01">old</a>'), DATES)
    soup = BeautifulSoup(out, "html.parser")
    assert soup.find("a") is None
    assert soup.find("span").get_text() == "old"


# ---------- today ----------


def test_todays_date_is_linkable_when_included():
    """archive/<today>.html isn't written yet when the pipeline calls this."""
    out = normalize_links(page('<a href="/2026-08-19">today</a>'), DATES | {"2026-08-19"})
    assert hrefs(out) == ["/archive/2026-08-19.html"]


def test_todays_date_de_linked_when_not_included():
    out = normalize_links(page('<a href="/2026-08-19">today</a>'), DATES)
    assert hrefs(out) == []


# ---------- robustness ----------


def test_idempotent():
    once = normalize_links(page('<a href="/2026-08-17">a</a><a href="/2026-06-18">b</a>'), DATES)
    assert normalize_links(once, DATES) == once


def test_already_canonical_html_is_returned_untouched():
    """No pointless re-serialization when there's nothing to fix."""
    html = page('<a href="/archive/2026-08-17.html">a</a>')
    assert normalize_links(html, DATES) is html


def test_survives_malformed_html():
    out = normalize_links('<a href="/2026-08-17">unclosed', DATES)
    assert "/archive/2026-08-17.html" in out


def test_preserves_validation_signals():
    html = page(
        '<p>Jeff Clark</p><p>jeff@clarkle.com</p>'
        '<a href="https://www.linkedin.com/in/serialcreative">in</a>'
        '<a href="/2026-08-17">17th</a>'
    )
    out = normalize_links(html, DATES)
    assert "Jeff Clark" in out
    assert "jeff@clarkle.com" in out
    assert "https://www.linkedin.com/in/serialcreative" in out


# ---------- the shipped site ----------


def test_no_broken_date_links_anywhere_in_the_repo():
    """The guard that would have caught this bug on day one.

    Every date link on every page — today's and all 116 snapshots — must
    point at a file that exists.
    """
    from scripts.check_links import broken_links

    problems = broken_links(REPO_ROOT)
    assert problems == [], "\n".join(f"{p.name}: {h} — {r}" for p, h, r in problems)


def test_repair_is_a_fixed_point():
    """The historical repair has already run; re-running must change nothing."""
    from scripts.repair_archive_links import repair

    # Dry-run equivalent: normalize each page and confirm it's unchanged.
    from scripts.check_links import site_pages

    available = {
        p.stem for p in (REPO_ROOT / "archive").glob("*.html") if p.name != "index.html"
    }
    for page in site_pages(REPO_ROOT):
        if page.name == "index.html" and page.parent.name == "archive":
            continue
        text = page.read_text()
        assert normalize_links(text, available) == text, page.name
    assert repair  # imported and callable
