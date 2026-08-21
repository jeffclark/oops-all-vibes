"""Tests for scripts/fetch_daily_inputs.py.

No network: the parsing helpers are tested directly against captured shapes,
and fetch_all is driven with stub source callables.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import scripts.fetch_daily_inputs as fdi  # noqa: E402


RUN_DATE = date(2026, 8, 21)


# --------------------------------------------------------------------------
# Library of Congress image selection
# --------------------------------------------------------------------------
def test_biggest_image_picks_widest_not_last():
    """The API's last variant is often the 150px thumbnail."""
    urls = [
        "https://x/b.jpg#h=766&w=1024",
        "https://x/a_150px.jpg#h=147&w=150",
    ]
    assert fdi._biggest_image(urls) == ("https://x/b.jpg#h=766&w=1024", 1024)


def test_biggest_image_handles_missing():
    assert fdi._biggest_image(None) == (None, 0)
    assert fdi._biggest_image([]) == (None, 0)


def test_fsa_usable_rejects_thumbnail_and_missing_location():
    thumbnail_only = {
        "title": "Boy Scouts, New York City",
        "date": "1942",
        "location": None,
        "image_url": ["https://x/3a16997_150px.jpg#h=147&w=150"],
    }
    assert fdi._fsa_usable(thumbnail_only) is False

    good = {
        "title": "Conversion. Flooring to gunstocks.",
        "date": "1942-01-01",
        "location": ["louisville", "kentucky"],
        "image_url": ["https://x/8b03307v.jpg#h=810&w=1024"],
    }
    assert fdi._fsa_usable(good) is True


# --------------------------------------------------------------------------
# Boston 311 text cleanup
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("SCH 8/27 Bed Bugs", "Bed Bugs"),
        ("DISP 8/19 Maintenance Complaint - Residential", "Maintenance Complaint - Residential"),
        ("  SCH 8/20/2026 Unsatisfactory Living Conditions", "Unsatisfactory Living Conditions"),
        ("Parking Enforcement", "Parking Enforcement"),
        (None, "Unspecified"),
    ],
)
def test_clean_case_title(raw, expected):
    assert fdi._clean_case_title(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "Case Closed. Closed date : Wed Aug 19 04:20:21 EDT 2026 Noted Abatement issued",
            "Abatement issued",
        ),
        (
            "Case Closed. Closed date : 2026-08-19 20:25:08.833 Case Resolved The violator(s) "
            "at this location was/were ticketed.",
            "The violator(s) at this location was/were ticketed.",
        ),
        (
            "Case Closed. Closed date : Wed Aug 19 09:41:47 EDT 2026 Resolved "
            "Searched area and no syringes were recovered.RA",
            "Searched area and no syringes were recovered.RA",
        ),
    ],
)
def test_strip_closure_stamp_keeps_only_what_a_person_typed(raw, expected):
    assert fdi._strip_closure_stamp(raw) == expected


# --------------------------------------------------------------------------
# JSON-LD parsing
# --------------------------------------------------------------------------
def test_ld_blocks_tolerates_literal_newlines_in_strings():
    """Municibid's listing block ships raw newlines inside description strings."""
    html = (
        '<script type="application/ld+json">'
        '{"@type": "Product", "description": "line one\nline two"}'
        "</script>"
    )
    blocks = fdi._ld_blocks(html)
    assert len(blocks) == 1
    assert blocks[0]["description"] == "line one\nline two"


def test_ld_blocks_skips_unparseable_without_raising():
    html = '<script type="application/ld+json">{not json at all</script>'
    assert fdi._ld_blocks(html) == []


# --------------------------------------------------------------------------
# Roster / assembly
# --------------------------------------------------------------------------
def test_load_roster_defaults_when_file_missing(tmp_path):
    state = fdi.load_roster(tmp_path / "nope.json")
    assert state["roster"] == fdi.DEFAULT_ROSTER
    assert state["builds_this_cycle"] == 0


def test_load_roster_falls_back_on_garbage(tmp_path):
    bad = tmp_path / "roster.json"
    bad.write_text("{{{not json")
    assert fdi.load_roster(bad)["roster"] == fdi.DEFAULT_ROSTER


def test_load_roster_reads_a_custom_roster(tmp_path):
    f = tmp_path / "roster.json"
    f.write_text(json.dumps({"roster": ["fsa", "hockey"], "retired": ["surplus"]}))
    state = fdi.load_roster(f)
    assert state["roster"] == ["fsa", "hockey"]
    assert state["retired"] == ["surplus"]
    assert state["retire_every_builds"] == fdi.RETIRE_EVERY_BUILDS


def test_fetch_all_records_failures_and_keeps_going(monkeypatch):
    """One dead source must never take the others down with it."""
    monkeypatch.setattr(fdi, "SOURCES", {
        "good": ("A good one", lambda s, r, d: {"value": 1}),
        "bad": ("A bad one", lambda s, r, d: (_ for _ in ()).throw(RuntimeError("boom"))),
    })
    payload = fdi.fetch_all(RUN_DATE, ["good", "bad"], seed=1)
    assert payload["inputs"]["good"]["data"] == {"value": 1}
    assert "bad" in payload["failures"]
    assert "boom" in payload["failures"]["bad"]
    assert payload["date"] == RUN_DATE.isoformat()


def test_fetch_all_flags_unknown_source_key(monkeypatch):
    monkeypatch.setattr(fdi, "SOURCES", {})
    payload = fdi.fetch_all(RUN_DATE, ["nonesuch"], seed=1)
    assert payload["failures"]["nonesuch"] == "unknown source key"


def test_every_default_roster_key_has_a_source():
    for key in fdi.DEFAULT_ROSTER:
        assert key in fdi.SOURCES, f"{key} is on the roster with no fetcher"


# --------------------------------------------------------------------------
# Contact details must not reach the prompt or the public page
# --------------------------------------------------------------------------
def test_closure_note_scrubs_worker_email():
    raw = ("Case Closed. Closed date : Wed Aug 19 04:20:21 EDT 2026 Noted "
           "I did email constituent regarding this issue. j.worker@example.invalid")
    out = fdi._strip_closure_stamp(raw)
    assert "@" not in out
    assert "[email]" in out
    assert "I did email constituent" in out


def test_closure_note_scrubs_phone_numbers():
    out = fdi._strip_closure_stamp("Closed. Call 617-555-0123 for details")
    assert "617" not in out
    assert "[phone]" in out


def test_fsa_rejects_records_located_only_in_the_united_states():
    """Every record carries 'united states'; alone it locates nothing."""
    generic = {"title": "Lawrence S. Knappen", "date": "1940-01-01",
               "location": ["united states"], "image_url": ["a#h=789&w=1024"]}
    assert fdi._fsa_usable(generic) is False

    located = dict(generic, location=["united states", "tulsa", "oklahoma"])
    assert fdi._fsa_usable(located) is True


def test_redact_contacts_covers_any_fetched_free_text():
    """Surplus descriptions carry addresses too, not just 311 notes."""
    raw = "Please email seller@example.invalid if you want a specific item, or call 610-555-0148."
    out = fdi._redact_contacts(raw)
    assert "@" not in out and "610" not in out
    assert "[email]" in out and "[phone]" in out
    assert "if you want a specific item" in out
