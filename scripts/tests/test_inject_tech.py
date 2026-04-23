"""Tests for scripts/inject_tech.py."""
from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.inject_tech import inject_tech  # noqa: E402


BASE_HTML = """<!DOCTYPE html>
<html><head><title>today</title></head><body><p>hi</p></body></html>"""


def test_with_code_adds_both_script_and_footer():
    out = inject_tech(BASE_HTML, "2026-04-22", "oops-all-vibes")
    assert 'data-goatcounter="https://oops-all-vibes.goatcounter.com/count"' in out
    assert 'src="//gc.zgo.at/count.js"' in out
    assert 'href="/prompts/2026-04-22.md"' in out
    assert "today's prompt" in out


def test_without_code_only_footer_no_script():
    out = inject_tech(BASE_HTML, "2026-04-22", None)
    assert "goatcounter" not in out.lower()
    assert 'href="/prompts/2026-04-22.md"' in out
    assert "today's prompt" in out


def test_empty_code_treated_as_no_script():
    out = inject_tech(BASE_HTML, "2026-04-22", "")
    assert "goatcounter" not in out.lower()
    assert 'href="/prompts/2026-04-22.md"' in out


def test_creates_head_when_missing():
    html_no_head = "<html><body><p>hi</p></body></html>"
    out = inject_tech(html_no_head, "2026-04-22", "oops-all-vibes")
    soup = BeautifulSoup(out, "html.parser")
    assert soup.find("head") is not None
    assert soup.find("head").find("script") is not None


def test_output_parses_as_valid_html():
    out = inject_tech(BASE_HTML, "2026-04-22", "oops-all-vibes")
    soup = BeautifulSoup(out, "html.parser")
    assert soup.find("body") is not None
    assert soup.find("head") is not None
    footer = soup.find("footer")
    assert footer is not None
    link = footer.find("a")
    assert link["href"] == "/prompts/2026-04-22.md"


def test_footer_appended_to_end_of_body():
    out = inject_tech(BASE_HTML, "2026-04-22", "oops-all-vibes")
    soup = BeautifulSoup(out, "html.parser")
    body_children = [c for c in soup.find("body").children if getattr(c, "name", None)]
    assert body_children[-1].name == "footer"


def test_injection_doesnt_break_existing_validation_signals():
    """The injected HTML must still contain whatever Georgia put in raw."""
    html = (
        "<!DOCTYPE html><html><head><title>today</title></head>"
        "<body><p>Jeff Clark</p><p>jeff@clarkle.com</p>"
        "<a href='https://www.linkedin.com/in/serialcreative'>LinkedIn</a></body></html>"
    )
    out = inject_tech(html, "2026-04-22", "oops-all-vibes")
    assert "Jeff Clark" in out
    assert "jeff@clarkle.com" in out
    assert "https://www.linkedin.com/in/serialcreative" in out
