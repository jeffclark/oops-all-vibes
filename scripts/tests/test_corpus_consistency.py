"""Tests for scripts/corpus/consistency.py and its stats block — story_018.

The classifier is faked; pair selection, idempotence and the rendering are real.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.build_stats_page import build_stats_page
from scripts.corpus import consistency

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class FakeClient:
    def __init__(self, verdicts=("consistent",)):
        self.verdicts = list(verdicts)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        verdict = self.verdicts[min(len(self.calls) - 1, len(self.verdicts) - 1)]
        body = json.dumps({"classification": verdict, "note": "because"})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)])


def pref(frame_id, when, verdict):
    return {"date": when, "frame_id": frame_id, "verdict": verdict, "confidence": 3}


def write_prefs(tmp_path: Path, rows):
    path = tmp_path / "preferences.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


# ------------------------------------------------------------ pair selection


def test_two_entries_twenty_days_apart_make_one_pair():
    entries = [pref("a-t1", "2026-01-01", "early"), pref("a-t1", "2026-01-21", "late")]
    pairs = consistency.eligible_pairs(entries)
    assert len(pairs) == 1
    assert pairs[0].gap_days == 20


def test_entries_closer_than_fourteen_days_are_not_classified():
    entries = [pref("a-t1", "2026-01-01", "early"), pref("a-t1", "2026-01-10", "late")]
    assert consistency.eligible_pairs(entries) == []


def test_a_frame_with_one_entry_makes_no_pair():
    assert consistency.eligible_pairs([pref("a-t1", "2026-01-01", "only")]) == []


def test_pairs_are_never_built_across_different_frames():
    entries = [pref("a-t1", "2026-01-01", "x"), pref("b-t1", "2026-02-01", "y")]
    assert consistency.eligible_pairs(entries) == []


def test_endpoints_and_adjacent_pairs_are_both_covered():
    entries = [
        pref("a-t1", "2026-01-01", "first"),
        pref("a-t1", "2026-02-01", "middle"),
        pref("a-t1", "2026-03-01", "last"),
    ]
    pairs = consistency.eligible_pairs(entries)
    spans = {(p.early_date, p.late_date) for p in pairs}
    assert ("2026-01-01", "2026-02-01") in spans   # adjacent
    assert ("2026-02-01", "2026-03-01") in spans   # adjacent
    assert ("2026-01-01", "2026-03-01") in spans   # endpoints


def test_entries_with_no_verdict_text_are_ignored():
    entries = [pref("a-t1", "2026-01-01", "  "), pref("a-t1", "2026-01-21", "late")]
    assert consistency.eligible_pairs(entries) == []


def test_an_unparseable_date_is_ignored_rather_than_crashing():
    entries = [pref("a-t1", "not-a-date", "x"), pref("a-t1", "2026-01-21", "late")]
    assert consistency.eligible_pairs(entries) == []


# ------------------------------------------------------------------ the run


def test_nothing_eligible_writes_nothing_and_exits_zero(tmp_path):
    prefs = write_prefs(tmp_path, [pref("a-t1", "2026-01-01", "only")])
    out = tmp_path / "consistency.jsonl"
    client = FakeClient()
    assert consistency.run(client, prefs, out) == 0
    assert client.calls == []
    assert not out.exists()


def test_no_preferences_file_at_all_is_fine(tmp_path):
    assert consistency.run(FakeClient(), tmp_path / "nope.jsonl", tmp_path / "out.jsonl") == 0


def test_a_classified_pair_is_written_with_both_verdicts_and_the_gap(tmp_path):
    prefs = write_prefs(tmp_path, [
        pref("a-t1", "2026-01-01", "early opinion"),
        pref("a-t1", "2026-01-21", "later opinion"),
    ])
    out = tmp_path / "consistency.jsonl"
    consistency.run(FakeClient(["reversed"]), prefs, out)

    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["classification"] == "reversed"
    assert row["early_verdict"] == "early opinion"
    assert row["late_verdict"] == "later opinion"
    assert row["gap_days"] == 20


def test_the_classification_is_always_one_of_exactly_four(tmp_path):
    prefs = write_prefs(tmp_path, [
        pref("a-t1", "2026-01-01", "e"), pref("a-t1", "2026-01-21", "l"),
    ])
    out = tmp_path / "consistency.jsonl"
    consistency.run(FakeClient(["consistent"]), prefs, out)
    row = json.loads(out.read_text().splitlines()[0])
    assert row["classification"] in consistency.OUTCOMES


def test_an_out_of_vocabulary_classification_is_rejected_not_recorded(tmp_path):
    prefs = write_prefs(tmp_path, [
        pref("a-t1", "2026-01-01", "e"), pref("a-t1", "2026-01-21", "l"),
    ])
    out = tmp_path / "consistency.jsonl"
    consistency.run(FakeClient(["vibes"]), prefs, out)
    assert out.read_text() == ""


def test_one_bad_pair_does_not_lose_the_others(tmp_path):
    prefs = write_prefs(tmp_path, [
        pref("a-t1", "2026-01-01", "e"), pref("a-t1", "2026-01-21", "l"),
        pref("b-t1", "2026-01-01", "e"), pref("b-t1", "2026-01-21", "l"),
    ])
    out = tmp_path / "consistency.jsonl"
    consistency.run(FakeClient(["vibes", "evolved"]), prefs, out)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["frame_id"] for r in rows] == ["b-t1"]


def test_rerunning_does_not_reclassify_or_duplicate(tmp_path):
    prefs = write_prefs(tmp_path, [
        pref("a-t1", "2026-01-01", "e"), pref("a-t1", "2026-01-21", "l"),
    ])
    out = tmp_path / "consistency.jsonl"
    consistency.run(FakeClient(["consistent"]), prefs, out)

    second = FakeClient(["reversed"])
    consistency.run(second, prefs, out)
    assert second.calls == []
    assert len(out.read_text().splitlines()) == 1


def test_a_new_entry_adds_only_the_new_pairs(tmp_path):
    rows = [pref("a-t1", "2026-01-01", "e"), pref("a-t1", "2026-01-21", "l")]
    prefs = write_prefs(tmp_path, rows)
    out = tmp_path / "consistency.jsonl"
    consistency.run(FakeClient(["consistent"]), prefs, out)

    rows.append(pref("a-t1", "2026-02-20", "later still"))
    write_prefs(tmp_path, rows)
    client = FakeClient(["evolved"])
    consistency.run(client, prefs, out)
    assert len(client.calls) == 2  # the new adjacent pair and the new endpoint pair
    assert len(out.read_text().splitlines()) == 3


def test_the_classifier_is_told_nothing_about_who_wrote_the_verdicts(tmp_path):
    prefs = write_prefs(tmp_path, [
        pref("a-t1", "2026-01-01", "e"), pref("a-t1", "2026-01-21", "l"),
    ])
    client = FakeClient(["consistent"])
    consistency.run(client, prefs, tmp_path / "out.jsonl")
    sent = client.calls[0]["messages"][0]["content"] + client.calls[0]["system"]
    assert "Georgia" not in sent
    assert client.calls[0]["model"] == "claude-haiku-4-5"


def test_no_blind_retest_was_reintroduced():
    """An earlier draft slipped a judged frame back in unannounced. It is gone."""
    src = (REPO_ROOT / "scripts" / "corpus" / "consistency.py").read_text()
    # this module only reads; it must never touch selection or the prompt
    assert "select" not in re.sub(r"#.*|\"\"\".*?\"\"\"", "", src, flags=re.S)
    assert "assemble_prompt" not in src


def test_select_is_untouched_by_this_story():
    src = (REPO_ROOT / "scripts" / "corpus" / "select.py").read_text()
    assert "consistency" not in src
    assert "blind" not in src.lower()


def test_consistency_is_not_imported_by_the_daily_pipeline():
    offenders = []
    for py in (REPO_ROOT / "scripts").glob("*.py"):
        if re.search(r"^\s*(import|from).*\bconsistency\b", py.read_text(), re.M):
            offenders.append(py.name)
    assert not offenders, offenders


def test_consistency_imports_nothing_from_the_daily_pipeline():
    src = (REPO_ROOT / "scripts" / "corpus" / "consistency.py").read_text()
    assert not re.search(r"^\s*from\s+scripts\.(?!corpus)", src, re.M)


# -------------------------------------------------------------- stats block


def _stats_repo(tmp_path: Path, rows=None):
    (tmp_path / "corpus").mkdir(parents=True, exist_ok=True)
    (tmp_path / "stats.jsonl").write_text(json.dumps({
        "date": "2026-08-25", "attempts": 1, "validation_failures": [],
        "api_errors": 0, "committed": True, "duration_ms": 1000,
    }) + "\n")
    if rows is not None:
        (tmp_path / "corpus" / "consistency.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows)
        )
    return tmp_path


def _record(frame_id, classification, gap):
    return {
        "frame_id": frame_id, "early_date": "2026-01-01", "late_date": "2026-06-01",
        "gap_days": gap, "early_verdict": "e", "late_verdict": "l",
        "classification": classification, "note": "n",
    }


def test_the_stats_page_renders_with_zero_pairs(tmp_path):
    build_stats_page(repo_root=_stats_repo(tmp_path))
    html = (tmp_path / "stats.html").read_text()
    assert "<h2>Corpus</h2>" in html
    assert "No pairs yet" in html


def test_the_stats_page_renders_the_four_way_split_and_longest_gap(tmp_path):
    rows = [
        _record("a-t1", "consistent", 20), _record("a-t1", "evolved", 60),
        _record("b-t1", "reversed", 150), _record("c-t1", "unrelated", 30),
    ]
    build_stats_page(repo_root=_stats_repo(tmp_path, rows))
    html = (tmp_path / "stats.html").read_text()
    for outcome in consistency.OUTCOMES:
        assert f">{outcome}</span>" in html
    assert "150d" in html
    assert "pairs compared" in html


def test_the_stats_page_names_the_most_drifted_anchors(tmp_path):
    rows = [_record("a-t1", "reversed", 20), _record("a-t1", "evolved", 40),
            _record("b-t1", "consistent", 20)]
    build_stats_page(repo_root=_stats_repo(tmp_path, rows))
    html = (tmp_path / "stats.html").read_text()
    assert "Most movement" in html
    assert "a-t1 (2)" in html
    assert "b-t1" not in html.split("Most movement")[1][:200]


def test_the_stats_page_does_not_editorialise_about_the_outcomes(tmp_path):
    rows = [_record("a-t1", "reversed", 200)]
    build_stats_page(repo_root=_stats_repo(tmp_path, rows))
    html = (tmp_path / "stats.html").read_text()
    assert "not the good outcome" in html
    assert "is not\nfailure" in html or "is not failure" in html


def test_a_corrupt_consistency_file_does_not_break_the_stats_page(tmp_path):
    root = _stats_repo(tmp_path)
    (root / "corpus" / "consistency.jsonl").write_text("{ not json\n")
    build_stats_page(repo_root=root)
    assert "No pairs yet" in (tmp_path / "stats.html").read_text()


def test_a_failed_corpus_verification_shows_on_the_stats_page(tmp_path):
    root = _stats_repo(tmp_path)
    (root / "corpus" / "verify.json").write_text(json.dumps({
        "checked_at": "2026-08-25", "ok": False,
        "corpus_verify_failed": True, "missing": ["bd-2014-t152"],
    }))
    build_stats_page(repo_root=root)
    html = (tmp_path / "stats.html").read_text()
    assert "corpus_verify_failed" in html
    assert "bd-2014-t152" in html


def test_a_passing_verification_says_so_quietly(tmp_path):
    root = _stats_repo(tmp_path)
    (root / "corpus" / "verify.json").write_text(json.dumps({
        "checked_at": "2026-08-25", "ok": True, "corpus_verify_failed": False, "missing": [],
    }))
    build_stats_page(repo_root=root)
    html = (tmp_path / "stats.html").read_text()
    assert "every file reference resolves" in html
    assert "corpus_verify_failed" not in html
