"""Tests for scripts/validate_output.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_output import (  # noqa: E402
    MAX_HTML_BYTES,
    MIN_BODY_TEXT_LEN,
    MIN_HTML_BYTES,
    validate_output,
)


FACTS = {
    "name": "Jeff Clark",
    "email": "jeff@clarkle.com",
    "linkedin_url": "https://www.linkedin.com/in/serialcreative",
    "linkedin_title": "Director of Product at LeagueApps",
    "projects": [
        {"title": "Autoscope", "description": "x", "link": "x", "image": "x"},
        {"title": "Currents", "description": "y", "link": "y", "image": "y"},
    ],
}
TODAY = "2026-04-22"


def _valid_html() -> str:
    body_text = "Here is a generous chunk of prose about Jeff Clark. " * 10
    return f"""<!DOCTYPE html>
<html><head><title>today</title></head>
<body>
  <h1>Jeff Clark</h1>
  <p>{body_text}</p>
  <p>Contact: jeff@clarkle.com</p>
  <p>LinkedIn: https://www.linkedin.com/in/serialcreative</p>
  <ul>
    <li>Autoscope</li>
    <li>Currents</li>
  </ul>
  {"<p>filler</p>" * 60}
</body>
</html>"""


def _valid_diary(date_str: str = TODAY, importance: int = 3) -> str:
    return (
        f"---\n"
        f"date: {date_str}\n"
        f"importance: {importance}\n"
        f"---\n\n"
        f"Today was a solid three. I built the thing and it mostly worked.\n"
    )


# ---------- happy path ----------


def test_valid_output_returns_true_with_empty_reasons():
    html = _valid_html()
    diary = _valid_diary()
    assert len(html.encode("utf-8")) >= MIN_HTML_BYTES, "fixture too small"
    ok, reasons = validate_output(html, diary, FACTS, TODAY)
    assert ok is True
    assert reasons == []


# ---------- HTML failures ----------


def test_missing_email():
    html = _valid_html().replace("jeff@clarkle.com", "redacted@example.com")
    ok, reasons = validate_output(html, _valid_diary(), FACTS, TODAY)
    assert ok is False
    assert any("jeff@clarkle.com" in r for r in reasons)


def test_missing_name():
    html = _valid_html().replace("Jeff Clark", "Someone Else")
    ok, reasons = validate_output(html, _valid_diary(), FACTS, TODAY)
    assert ok is False
    assert any("Jeff Clark" in r and "inviolable" in r for r in reasons)


def test_missing_linkedin_url():
    html = _valid_html().replace(
        "https://www.linkedin.com/in/serialcreative", "https://example.com"
    )
    ok, reasons = validate_output(html, _valid_diary(), FACTS, TODAY)
    assert ok is False
    assert any("LinkedIn URL" in r for r in reasons)


def test_missing_project_title():
    html = _valid_html().replace("Autoscope", "Autonotscope")
    ok, reasons = validate_output(html, _valid_diary(), FACTS, TODAY)
    assert ok is False
    assert any("Autoscope" in r for r in reasons)


def test_html_too_small():
    ok, reasons = validate_output("<html><body>tiny</body></html>", _valid_diary(), FACTS, TODAY)
    assert ok is False
    assert any("too small" in r for r in reasons)


def test_html_too_large():
    huge = "<html><body>" + ("x" * (MAX_HTML_BYTES + 10)) + "</body></html>"
    ok, reasons = validate_output(huge, _valid_diary(), FACTS, TODAY)
    assert ok is False
    assert any("too large" in r for r in reasons)


def test_html_no_body_tag():
    # Pad to pass size check but omit <body>
    html = "<html>" + "<p>filler</p>" * 200 + "</html>"
    ok, reasons = validate_output(html, _valid_diary(), FACTS, TODAY)
    assert ok is False
    assert any("no <body>" in r for r in reasons)


def test_body_text_too_short():
    # Has <body> but only whitespace/minimal content; size padded with comments
    padding = "<!-- " + ("x" * 1500) + " -->"
    html = f"<html>{padding}<body>hi</body></html>"
    ok, reasons = validate_output(html, _valid_diary(), FACTS, TODAY)
    assert ok is False
    assert any("too little text" in r or "at least" in r for r in reasons)


# ---------- Diary failures ----------


def test_diary_missing_frontmatter():
    diary = "Just some prose, no frontmatter here.\n"
    ok, reasons = validate_output(_valid_html(), diary, FACTS, TODAY)
    assert ok is False
    assert any("frontmatter" in r for r in reasons)


def test_diary_wrong_date():
    diary = _valid_diary(date_str="2026-04-21")
    ok, reasons = validate_output(_valid_html(), diary, FACTS, TODAY)
    assert ok is False
    assert any("2026-04-21" in r and "2026-04-22" in r for r in reasons)


def test_diary_importance_out_of_range():
    diary = _valid_diary(importance=7)
    ok, reasons = validate_output(_valid_html(), diary, FACTS, TODAY)
    assert ok is False
    assert any("importance" in r and "out of range" in r for r in reasons)


def test_diary_importance_zero_out_of_range():
    diary = _valid_diary(importance=0)
    ok, reasons = validate_output(_valid_html(), diary, FACTS, TODAY)
    assert ok is False
    assert any("importance" in r for r in reasons)


def test_diary_importance_not_integer():
    diary = "---\ndate: 2026-04-22\nimportance: high\n---\n\n" + ("body " * 20)
    ok, reasons = validate_output(_valid_html(), diary, FACTS, TODAY)
    assert ok is False
    assert any("importance" in r and "integer" in r for r in reasons)


def test_diary_missing_importance():
    diary = "---\ndate: 2026-04-22\n---\n\n" + ("body " * 20)
    ok, reasons = validate_output(_valid_html(), diary, FACTS, TODAY)
    assert ok is False
    assert any("importance" in r for r in reasons)


def test_diary_missing_date():
    diary = "---\nimportance: 3\n---\n\n" + ("body " * 20)
    ok, reasons = validate_output(_valid_html(), diary, FACTS, TODAY)
    assert ok is False
    assert any("'date'" in r or "date field" in r.lower() for r in reasons)


def test_diary_body_too_short():
    diary = "---\ndate: 2026-04-22\nimportance: 3\n---\n\nhi\n"
    ok, reasons = validate_output(_valid_html(), diary, FACTS, TODAY)
    assert ok is False
    assert any("body is too short" in r for r in reasons)


def test_diary_date_as_yaml_date_type_compared_correctly():
    # PyYAML parses bare YYYY-MM-DD as datetime.date, not str
    diary = "---\ndate: 2026-04-22\nimportance: 3\n---\n\n" + ("body " * 20)
    ok, reasons = validate_output(_valid_html(), diary, FACTS, TODAY)
    assert ok is True, reasons


# ---------- multiple failures collected ----------


def test_multiple_failures_collected_not_short_circuited():
    # HTML missing email AND diary with bad importance AND bad date
    html = _valid_html().replace("jeff@clarkle.com", "x@x")
    diary = _valid_diary(date_str="2026-04-01", importance=99)
    ok, reasons = validate_output(html, diary, FACTS, TODAY)
    assert ok is False
    # Should have at least 3 distinct failure reasons
    assert len(reasons) >= 3
    joined = " | ".join(reasons)
    assert "jeff@clarkle.com" in joined
    assert "2026-04-01" in joined
    assert "99" in joined
