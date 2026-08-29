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


def test_a_corrupt_roster_is_a_hard_stop_not_a_silent_reset(tmp_path):
    """Falling back to defaults would un-retire every source and erase the record."""
    bad = tmp_path / "roster.json"
    bad.write_text("{{{not json")
    with pytest.raises(fdi.RosterError):
        fdi.load_roster(bad)
    # And the corrupt file is left exactly as it was, not overwritten.
    assert bad.read_text() == "{{{not json"


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


# --------------------------------------------------------------------------
# Regressions found by code review
# --------------------------------------------------------------------------
class _FakeHT:
    """Stands in for the HockeyTech seasons feed."""

    def __init__(self, seasons):
        self.seasons = seasons

    def __call__(self, session, view, **params):
        return {"Seasons": self.seasons}


# Real names and ids from the live ACHA feed, including the spring tournament
# blocks whose ids outrank the regular season.
ACHA_SEASONS = [
    {"season_id": "60", "season_name": "2025-2026 Men's Divisions"},
    {"season_id": "63", "season_name": "2026 Men's D2 Regionals"},
    {"season_id": "66", "season_name": "2026 Men's Division 2 Nationals Pool Play"},
    {"season_id": "67", "season_name": "2026 Men's Division 2 Nationals Final Four"},
    {"season_id": "68", "season_name": "2026 Men's Division 1 Nationals"},
    {"season_id": "71", "season_name": "2026 Men's Division 3 National Final Four"},
    {"season_id": "74", "season_name": "2026-27 Women's Divisions"},
]


def test_season_picker_ignores_postseason_blocks(monkeypatch):
    """Tournament seasons get higher ids than the regular season every spring."""
    monkeypatch.setattr(fdi, "_ht", _FakeHT(ACHA_SEASONS))
    season_id, name = fdi._current_mens_season(session=None)
    assert (season_id, name) == ("60", "2025-2026 Men's Divisions")


def test_season_picker_prefers_the_newest_regular_season(monkeypatch):
    monkeypatch.setattr(fdi, "_ht", _FakeHT(
        ACHA_SEASONS + [{"season_id": "73", "season_name": "2026-27 Men's Divisions"}]
    ))
    assert fdi._current_mens_season(session=None)[0] == "73"


def test_season_picker_never_returns_a_womens_season(monkeypatch):
    monkeypatch.setattr(fdi, "_ht", _FakeHT(
        [{"season_id": "74", "season_name": "2026-27 Women's Divisions"},
         {"season_id": "73", "season_name": "2026-27 Men's Divisions"}]
    ))
    assert fdi._current_mens_season(session=None)[0] == "73"


# --------------------------------------------------------------------------
# Retirement is binding: she chooses, the source is gone
# --------------------------------------------------------------------------
def _roster(tmp_path: Path, **over) -> Path:
    f = tmp_path / "roster.json"
    state = {"roster": list(fdi.DEFAULT_ROSTER), "retire_every_builds": 30,
             "builds_this_cycle": 30, "overdue_builds": 3, "retired": [],
             "full_roster_size": 5}
    state.update(over)
    f.write_text(json.dumps(state))
    return f


def test_retirement_removes_the_source_and_records_the_reason(tmp_path):
    f = _roster(tmp_path)
    state = fdi.apply_retirement("surplus", "  It repeats.\n Every week the same chairs. ",
                                 date(2026, 9, 20), f)
    assert "surplus" not in state["roster"]
    assert len(state["roster"]) == 4
    assert state["retired"] == [{
        "key": "surplus", "date": "2026-09-20",
        "reason": "It repeats. Every week the same chairs.",
    }]
    # Written through, not just returned.
    assert json.loads(f.read_text())["roster"] == state["roster"]


def test_retirement_opens_a_new_cycle(tmp_path):
    f = _roster(tmp_path)
    state = fdi.apply_retirement("hockey", "done", date(2026, 9, 20), f)
    assert state["builds_this_cycle"] == 0
    assert state["overdue_builds"] == 0
    assert state["cycles_completed"] == 1


def test_retirement_rejects_a_source_not_on_the_roster(tmp_path):
    f = _roster(tmp_path)
    with pytest.raises(fdi.RetirementError, match="not on the roster"):
        fdi.apply_retirement("weather", "never existed", date(2026, 9, 20), f)
    assert json.loads(f.read_text())["roster"] == fdi.DEFAULT_ROSTER


def test_retirement_will_not_empty_the_roster(tmp_path):
    f = _roster(tmp_path, roster=["fsa"])
    with pytest.raises(fdi.RetirementError, match="empty the roster"):
        fdi.apply_retirement("fsa", "all of it", date(2026, 9, 20), f)


@pytest.mark.parametrize("diary,expected", [
    ("---\ndate: 2026-09-20\nimportance: 4\nretiring: surplus\n---\n\nThe chairs again.",
     ("surplus", "The chairs again.")),
    ('---\nretiring: "hockey"\n---\nBody.', ("hockey", "Body.")),
    ("---\ndate: 2026-09-20\nimportance: 2\n---\n\nOrdinary day.", None),
    ("no frontmatter at all", None),
    ("", None),
])
def test_retirement_declaration_is_read_from_the_log_frontmatter(diary, expected):
    assert fdi.retirement_from_diary(diary) == expected


def test_a_retirement_named_in_the_body_does_not_count():
    """Only the frontmatter key is binding — prose about retiring isn't a decision."""
    diary = "---\ndate: 2026-09-20\n---\n\nI think I'd retire surplus if I had to."
    assert fdi.retirement_from_diary(diary) is None


# --------------------------------------------------------------------------
# Adversarial review regressions
# --------------------------------------------------------------------------
def test_fsa_sampling_stays_inside_the_deep_paging_cap():
    """loc.gov 400s past sp=4000; paging beyond it spent 42% of attempts on errors."""
    assert fdi.FSA_SAMPLE_PAGES <= fdi.FSA_MAX_PAGE
    reach = fdi.FSA_SAMPLE_PAGES * fdi.FSA_PAGE_SIZE
    assert reach >= 100_000, f"only {reach:,} records reachable"


def test_only_one_retirement_per_day(tmp_path):
    """A re-dispatched workflow generates a second diary naming a second source."""
    f = _roster(tmp_path)
    fdi.apply_retirement("surplus", "bored", date(2026, 9, 20), f)
    # The cycle reset already blocks it; force the cycle back to due to prove the
    # same-day guard independently.
    state = json.loads(f.read_text())
    state["builds_this_cycle"] = state["retire_every_builds"]
    f.write_text(json.dumps(state))
    with pytest.raises(fdi.RetirementError, match="already retired"):
        fdi.apply_retirement("hockey", "also bored", date(2026, 9, 20), f)
    assert len(json.loads(f.read_text())["roster"]) == 4


def test_a_retirement_in_a_later_cycle_is_allowed(tmp_path):
    f = _roster(tmp_path)
    fdi.apply_retirement("surplus", "bored", date(2026, 9, 20), f)
    state = json.loads(f.read_text())
    state["builds_this_cycle"] = state["retire_every_builds"]   # next cycle expires
    f.write_text(json.dumps(state))
    state = fdi.apply_retirement("hockey", "next time", date(2026, 10, 20), f)
    assert state["roster"] == ["fsa", "civic", "register"]


def test_a_retirement_off_cycle_is_refused(tmp_path):
    """Otherwise any induced `retiring:` line retires a source on any day."""
    f = _roster(tmp_path, builds_this_cycle=3)
    with pytest.raises(fdi.RetirementError, match="no retirement due"):
        fdi.apply_retirement("surplus", "not due yet", date(2026, 8, 24), f)
    assert json.loads(f.read_text())["roster"] == fdi.DEFAULT_ROSTER


@pytest.mark.parametrize("diary", [
    # `retiring:` inside a block scalar is prose, not a decision.
    "---\ndate: 2026-09-20\nsummary: |\n  I keep retiring: hockey in my head.\n---\n\nBody.",
    # nested under another mapping is not a top-level declaration
    "---\ndate: 2026-09-20\nmeta:\n  retiring: hockey\n---\n\nBody.",
    # not a scalar string
    "---\nretiring: [hockey]\n---\n\nBody.",
    # not a plausible source key
    "---\nretiring: the whole idea honestly\n---\n\nBody.",
])
def test_retirement_declaration_ignores_non_declarations(diary):
    assert fdi.retirement_from_diary(diary) is None


def test_retirement_declaration_still_reads_a_real_one():
    diary = "---\ndate: 2026-09-20\nimportance: 4\nretiring: surplus\n---\n\nThe chairs again."
    assert fdi.retirement_from_diary(diary) == ("surplus", "The chairs again.")


def test_311_resource_id_must_look_like_a_uuid(monkeypatch):
    """The id is interpolated into SQL and arrives from a remote API."""
    class _Resp:
        status_code = 200

        def raise_for_status(self): pass

        def json(self):
            return {"result": {"resources": [
                {"format": "CSV", "name": "311 Service Requests - 2026",
                 "id": 'x" UNION SELECT 1 --'},
            ]}}

    class _Session:
        def get(self, *a, **k): return _Resp()

    got = fdi._resolve_311_resource(_Session(), 2026)
    assert got == fdi.CKAN_311_FALLBACK_RESOURCE


# --------------------------------------------------------------------------
# Round-2 adversarial audit regressions
# --------------------------------------------------------------------------
@pytest.mark.parametrize("raw,gone", [
    ("Rodent activity found at 6 Taft St", "6 Taft St"),
    ("Inspected 1234 Blue Hill Avenue and found nothing", "1234 Blue Hill Avenue"),
    ("Abated at 41 Ruggles Street", "41 Ruggles Street"),
])
def test_closure_notes_drop_street_addresses(raw, gone):
    """The docstring promised addresses were dropped; only the structured column was."""
    out = fdi._redact_contacts(raw)
    assert gone not in out
    assert "[address]" in out


@pytest.mark.parametrize("keep", [
    "Abatement issued",
    "Searched area and no syringes were recovered.RA",
    "Case closed 3 units inspected",
    "Needle recovered. JT",
])
def test_address_redaction_leaves_ordinary_notes_alone(keep):
    assert fdi._redact_contacts(keep) == keep


def test_missing_roster_beside_existing_payloads_is_a_hard_stop(tmp_path):
    """A roster that vanished while payloads remain is state loss, not a cold start."""
    (tmp_path / "2026-08-21.json").write_text("{}")
    with pytest.raises(fdi.RosterError, match="refusing to rebuild"):
        fdi.load_roster(tmp_path / "roster.json")


def test_a_genuine_cold_start_still_works(tmp_path):
    state = fdi.load_roster(tmp_path / "roster.json")
    assert state["roster"] == fdi.DEFAULT_ROSTER
    assert state["builds_this_cycle"] == 0


def test_fsa_does_not_retry_into_a_rate_limit():
    """loc.gov documents a one-hour block for exceeding its rate limit."""
    calls = []

    class _R:
        status_code = 429
        text = ""
        headers = {}

        def raise_for_status(self): raise AssertionError("should not be reached")

        def json(self): raise AssertionError("should not be reached")

    class _S:
        def get(self, url, **kw):
            calls.append(url)
            return _R()

    with pytest.raises(ValueError, match="rate limited"):
        fdi.fetch_fsa(_S(), __import__("random").Random(0))
    assert len(calls) == 1, f"retried into a 429 {len(calls)} times"


# ---------- the countdown counts days, not invocations ----------
#
# This step runs before run_georgia, so it sits outside that script's
# already-built guard. On 2026-08-28 two builds landed on one date and
# builds_this_cycle went 5 -> 7, moving the retirement demand a day early.


def _drive_main(monkeypatch, tmp_path, roster_file, run_date: str) -> dict:
    """Run main() for one date with the network and output paths stubbed out."""
    monkeypatch.setattr(fdi, "ROSTER_FILE", roster_file)
    monkeypatch.setattr(fdi, "INPUTS_DIR", tmp_path / "inputs")
    monkeypatch.setattr(
        fdi, "load_roster", lambda *a, **k: json.loads(roster_file.read_text())
    )
    monkeypatch.setattr(
        fdi, "fetch_all", lambda *a, **k: {"inputs": {"fsa": {}}, "failures": {}}
    )
    assert fdi.main(["--date", run_date]) == 0
    return json.loads(roster_file.read_text())


def test_a_second_build_on_the_same_date_does_not_tick_the_countdown(
    monkeypatch, tmp_path
):
    f = _roster(tmp_path, builds_this_cycle=5, overdue_builds=0)

    first = _drive_main(monkeypatch, tmp_path, f, "2026-08-28")
    assert first["builds_this_cycle"] == 6

    second = _drive_main(monkeypatch, tmp_path, f, "2026-08-28")
    assert second["builds_this_cycle"] == 6, "a re-dispatch burned a build"


def test_the_next_day_still_ticks_it(monkeypatch, tmp_path):
    """The guard is per-date, not a one-shot latch."""
    f = _roster(tmp_path, builds_this_cycle=5, overdue_builds=0)

    _drive_main(monkeypatch, tmp_path, f, "2026-08-28")
    _drive_main(monkeypatch, tmp_path, f, "2026-08-28")
    third = _drive_main(monkeypatch, tmp_path, f, "2026-08-29")
    assert third["builds_this_cycle"] == 7


def test_the_counted_date_round_trips_through_the_roster_file(monkeypatch, tmp_path):
    f = _roster(tmp_path, builds_this_cycle=5, overdue_builds=0)
    state = _drive_main(monkeypatch, tmp_path, f, "2026-08-28")
    assert state["last_counted_date"] == "2026-08-28"


def test_a_rebuild_does_not_double_count_an_overdue_retirement(monkeypatch, tmp_path):
    """At the cap, the overdue counter is the thing that escalates. Once per day."""
    f = _roster(tmp_path, builds_this_cycle=30, overdue_builds=2)

    first = _drive_main(monkeypatch, tmp_path, f, "2026-08-28")
    assert first["overdue_builds"] == 3

    second = _drive_main(monkeypatch, tmp_path, f, "2026-08-28")
    assert second["overdue_builds"] == 3, "a re-dispatch escalated the demand twice"
