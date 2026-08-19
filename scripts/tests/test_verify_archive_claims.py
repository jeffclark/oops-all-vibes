"""Tests for scripts/verify_archive_claims.py."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.verify_archive_claims import (  # noqa: E402
    hard_failures,
    load_truth,
    soft_failures,
    verify_archive_claims,
)


SNAPSHOTS = ["2026-04-23", "2026-04-24", "2026-04-26"]  # 04-25 is a gap
IMPORTANCE = {"2026-04-23": 5, "2026-04-24": 2, "2026-04-26": 3}
TODAY = "2026-04-27"


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "archive").mkdir()
    (tmp_path / "log").mkdir()
    (tmp_path / "archive" / "index.html").write_text("<html></html>")
    for d in SNAPSHOTS:
        (tmp_path / "archive" / f"{d}.html").write_text("<html></html>")
        (tmp_path / "log" / f"{d}.md").write_text(
            f"---\ndate: {d}\nimportance: {IMPORTANCE[d]}\n---\n\nbody.\n"
        )
    return tmp_path


def page(body: str) -> str:
    return f"<!DOCTYPE html><html><body>{body}</body></html>"


def check(tmp_path: Path, body: str, today_importance: int | None = None):
    return verify_archive_claims(page(body), _repo(tmp_path), TODAY, today_importance)


def messages(found):
    return " | ".join(d.message for d in found)


# ---------- ground truth ----------


def test_truth_includes_today_whose_snapshot_isnt_written_yet(tmp_path):
    truth = load_truth(_repo(tmp_path), TODAY, today_importance=4)
    assert truth.dates == SNAPSHOTS + [TODAY]
    assert truth.count == 4
    assert truth.day_number("2026-04-26") == 3
    assert truth.importance[TODAY] == 4


def test_truth_ignores_archive_index(tmp_path):
    assert "index" not in load_truth(_repo(tmp_path), TODAY).dates


# ---------- HARD: structural claims ----------


def test_entry_for_a_day_that_never_happened_is_hard(tmp_path):
    found = check(tmp_path, '<li data-archive-date="2026-04-25">day 3</li>')
    assert len(hard_failures(found)) == 1
    assert "2026-04-25" in messages(hard_failures(found))


def test_link_to_a_day_that_never_happened_is_hard(tmp_path):
    found = check(tmp_path, '<a href="/archive/2026-04-25.html">gap</a>')
    assert len(hard_failures(found)) == 1


def test_malformed_entry_date_is_hard(tmp_path):
    found = check(tmp_path, '<li data-archive-date="last tuesday">x</li>')
    assert len(hard_failures(found)) == 1


def test_truthful_page_is_clean(tmp_path):
    body = (
        '<section id="archive"><h2>The Archive — 4 days</h2>'
        '<li class="imp-5" data-archive-date="2026-04-23">Day 1 — April 23</li>'
        '<li class="imp-2" data-archive-date="2026-04-24">Day 2 — April 24</li>'
        '<li class="imp-3" data-archive-date="2026-04-26">Day 3 — April 26</li>'
        '<li class="imp-4" data-archive-date="2026-04-27">Day 4 — April 27</li>'
        "</section>"
    )
    assert check(tmp_path, body, today_importance=4) == []


def test_todays_own_entry_is_not_treated_as_invented(tmp_path):
    found = check(tmp_path, f'<li data-archive-date="{TODAY}">today</li>')
    assert hard_failures(found) == []


# ---------- SOFT: claims read out of the text ----------


def test_wrong_day_number_is_soft(tmp_path):
    found = check(tmp_path, '<li data-archive-date="2026-04-26">Day 4 — April 26</li>')
    assert hard_failures(found) == []
    assert "it's day 3" in messages(soft_failures(found))


def test_day_number_counts_archived_days_not_calendar_days(tmp_path):
    """04-25 is missing, so 04-26 is day 3, not day 4."""
    found = check(tmp_path, '<li data-archive-date="2026-04-26">Day 3</li>')
    assert "it's day" not in messages(soft_failures(found))


def test_wrong_importance_from_class_is_soft(tmp_path):
    found = check(tmp_path, '<li class="imp-1" data-archive-date="2026-04-23">Day 1</li>')
    assert hard_failures(found) == []
    assert "importance 5" in messages(soft_failures(found))


def test_wrong_importance_from_attribute_is_soft(tmp_path):
    found = check(
        tmp_path,
        '<li data-archive-importance="1" data-archive-date="2026-04-23">Day 1</li>',
    )
    assert "importance 5" in messages(soft_failures(found))


def test_wrong_total_count_is_soft(tmp_path):
    found = check(tmp_path, '<section id="archive"><h2>99 days of this</h2></section>')
    assert hard_failures(found) == []
    assert "99 days" in messages(soft_failures(found))


def test_rendering_more_entries_than_exist_is_soft(tmp_path):
    body = "".join(f'<li data-archive-date="{d}">x</li>' for d in SNAPSHOTS)
    found = check(tmp_path, body)  # 3 entries, archive has 4 days
    assert "3 archive entries" in messages(soft_failures(found))


def test_text_date_for_a_missing_day_is_soft(tmp_path):
    found = check(tmp_path, '<section id="archive"><p>April 25 was a good one.</p></section>')
    assert hard_failures(found) == []
    assert "2026-04-25" in messages(soft_failures(found))


def test_iso_text_date_for_a_missing_day_is_soft(tmp_path):
    found = check(tmp_path, '<section id="archive"><p>2026-04-25 was good.</p></section>')
    assert "2026-04-25" in messages(soft_failures(found))


def test_title_attribute_text_is_scanned(tmp_path):
    """Today's real page hides its date claims in tooltips."""
    found = check(tmp_path, '<section id="archive"><i title="Day 3 — April 25"></i></section>')
    assert "2026-04-25" in messages(soft_failures(found))


# ---------- false positives ----------


def test_unrelated_day_counts_outside_the_archive_are_ignored(tmp_path):
    """The real page carries '60 days of silence from Jeff' in a stats block
    next to the archive. That is not a claim about the archive's size."""
    body = (
        '<div class="sidebar">'
        '<section id="archive"><li data-archive-date="2026-04-23">Day 1</li></section>'
        '<section class="stats"><p>60 days of silence from Jeff</p></section>'
        "</div>"
    )
    found = check(tmp_path, body)
    assert "60 days" not in messages(found)


def test_prose_dates_outside_the_archive_are_ignored(tmp_path):
    body = '<p>April 25 was my birthday.</p><section id="archive"></section>'
    assert "2026-04-25" not in messages(check(tmp_path, body))


def test_dates_outside_the_archive_range_are_ignored(tmp_path):
    body = '<section id="archive"><p>Founded 1994-03-02, and 2030-01-01 is far off.</p></section>'
    found = check(tmp_path, body)
    assert "1994" not in messages(found)
    assert "2030" not in messages(found)


def test_non_archive_links_are_ignored(tmp_path):
    """A /log/ link is date-shaped but is not an archive link, so it must
    never be reported as a missing snapshot."""
    body = (
        '<a href="/log/2026-04-23.md">log</a>'
        '<a href="mailto:jeff@clarkle.com">x</a>'
        '<a href="https://www.linkedin.com/in/serialcreative">in</a>'
    )
    assert check(tmp_path, body) == []


def test_dead_internal_link_is_soft_not_hard(tmp_path):
    """A link to a page that doesn't exist is real breakage, but it isn't the
    site lying about its archive, so it ships with a warning."""
    found = check(tmp_path, '<a href="/about">About</a>')
    assert hard_failures(found) == []
    assert "/about" in messages(soft_failures(found))


def test_dead_internal_link_reported_once_per_href(tmp_path):
    body = '<a href="/about">a</a><a href="/about">b</a>'
    assert len([d for d in check(tmp_path, body) if "/about" in d.message]) == 1


def test_links_to_files_this_run_creates_are_not_reported(tmp_path):
    """The injected footer points at today's log and prompt, and the archive
    index is rebuilt — none of which exist on disk when the gate runs."""
    body = (
        f'<a href="/log/{TODAY}.md">log</a>'
        f'<a href="/prompts/{TODAY}.md">prompt</a>'
        '<a href="/archive/">archive</a>'
    )
    assert check(tmp_path, body) == []


# ---------- coverage ----------


def test_untagged_archive_reports_that_checks_could_not_run(tmp_path):
    found = check(tmp_path, '<a href="/archive/2026-04-23.html">Day 1</a>')
    assert hard_failures(found) == []
    assert "data-archive-date" in messages(soft_failures(found))


def test_no_archive_on_the_page_reports_nothing(tmp_path):
    assert check(tmp_path, "<p>Just a page.</p>") == []


def test_tagged_page_gets_no_coverage_warning(tmp_path):
    found = check(tmp_path, '<li data-archive-date="2026-04-23">Day 1</li>')
    assert "data-archive-date" not in messages(found)


def test_archive_rendered_as_bare_prose_does_not_pass_in_silence(tmp_path):
    """No links, no tags, no element naming itself archive. Before this the
    page named three archive days and got zero checks and zero warnings."""
    found = check(tmp_path, "<p>Day 1 — April 23. Day 2 — April 24. Day 3 — April 25.</p>")
    assert "data-archive-date" in messages(soft_failures(found))


def test_prose_archive_still_reports_the_invented_day(tmp_path):
    found = check(tmp_path, "<p>Day 3 — April 25 was a good one.</p>")
    assert "data-archive-date" in messages(soft_failures(found))


def test_page_with_no_archive_content_stays_silent(tmp_path):
    """inject_tech puts the word 'archive' in every page's footer, so the
    trigger has to be a named day, not the word itself."""
    body = '<p>Some writing.</p><footer><a href="/archive/">archive</a></footer>'
    assert check(tmp_path, body) == []
