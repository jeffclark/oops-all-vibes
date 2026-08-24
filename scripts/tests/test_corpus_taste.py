"""Tests for story_017 — the <taste> tag, its sentinels, and the preference log.

Covers the three places it lives: the prompt (assemble_prompt), the validation
(validate_output.validate_taste), the append (write_outputs), and the orchestration
that must never let any of it cost a day (run_georgia).
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from anthropic import APIError

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import scripts.run_georgia as run_module  # noqa: E402
import scripts.write_outputs as wo  # noqa: E402
from scripts.assemble_prompt import (  # noqa: E402
    CORPUS_DARK_SENTINEL,
    DAY_1_TASTE_SENTINEL,
    assemble_prompt,
    build_taste_block,
    load_preferences,
)
from scripts.call_model import ModelResult  # noqa: E402
from scripts.corpus import select as corpus_select  # noqa: E402
from scripts.validate_output import validate_taste  # noqa: E402

TODAY = "2026-08-25"
DAY = date(2026, 8, 25)
SHOWN = ["bd-2014-t152", "sv-2018-t88", "cad-1987-t201"]


def pref(frame_id, when, verdict="it holds", confidence=3, compared_to=None):
    return {
        "date": when,
        "frame_id": frame_id,
        "verdict": verdict,
        "compared_to": compared_to,
        "confidence": confidence,
    }


# ------------------------------------------------------------------ sentinels


def test_frames_shown_with_no_history_is_the_day_1_taste_sentinel():
    assert build_taste_block([], SHOWN, DAY) == DAY_1_TASTE_SENTINEL


def test_the_day_1_taste_sentinel_does_not_claim_she_is_new():
    """She is ~120 days into the project the first time this fires."""
    text = DAY_1_TASTE_SENTINEL.lower()
    assert "these images are new" in text
    for wrong in ("your first day", "first day online", "waking up", "just woke"):
        assert wrong not in text


def test_no_frames_shown_is_the_corpus_dark_sentinel():
    assert build_taste_block([pref("x", "2026-08-24")], [], DAY) == CORPUS_DARK_SENTINEL


def test_the_dark_sentinel_says_it_is_expected_back():
    text = CORPUS_DARK_SENTINEL.lower()
    assert "didn't load" in text
    assert "tomorrow" in text


def test_partial_history_is_the_ordinary_case_and_gets_neither_sentinel():
    entries = [pref(SHOWN[0], "2026-08-20")]
    block = build_taste_block(entries, SHOWN, DAY)
    assert block not in (DAY_1_TASTE_SENTINEL, CORPUS_DARK_SENTINEL)
    assert SHOWN[0] in block


def test_once_entries_exist_no_sentinel_comes_back():
    entries = [pref(f, "2026-08-24") for f in SHOWN]
    assert build_taste_block(entries, SHOWN, DAY) not in (
        DAY_1_TASTE_SENTINEL,
        CORPUS_DARK_SENTINEL,
    )


# ---------------------------------------------------------- history selection


def test_every_prior_verdict_on_a_frame_shown_today_comes_back_however_old():
    """Being shown what you thought of this exact frame months ago is the point."""
    entries = [pref(SHOWN[0], "2026-01-02", verdict="ancient opinion")]
    block = build_taste_block(entries, SHOWN, DAY)
    assert "ancient opinion" in block


def test_recent_verdicts_on_other_frames_come_back_too():
    entries = [pref("other-t1", "2026-08-20", verdict="recent elsewhere")]
    block = build_taste_block(entries, SHOWN, DAY)
    assert "recent elsewhere" in block


def test_old_verdicts_on_frames_not_shown_today_are_left_out():
    entries = [
        pref("other-t1", "2026-01-02", verdict="old elsewhere"),
        pref(SHOWN[0], "2026-08-24", verdict="today's frame"),
    ]
    block = build_taste_block(entries, SHOWN, DAY)
    assert "old elsewhere" not in block
    assert "today's frame" in block


def test_a_comparison_survives_into_the_history_she_reads():
    entries = [pref(SHOWN[0], "2026-08-24", compared_to=SHOWN[1])]
    assert f"against {SHOWN[1]}" in build_taste_block(entries, SHOWN, DAY)


def test_a_corrupt_preference_line_does_not_lose_the_good_ones(tmp_path, capsys):
    path = tmp_path / "preferences.jsonl"
    path.write_text(
        json.dumps(pref("a-t1", "2026-08-24")) + "\n"
        "{ not json\n"
        + json.dumps(pref("b-t1", "2026-08-24")) + "\n"
    )
    entries = load_preferences(path)
    assert [e["frame_id"] for e in entries] == ["a-t1", "b-t1"]
    assert "not JSON" in capsys.readouterr().err


def test_a_missing_preference_file_is_simply_no_history(tmp_path):
    assert load_preferences(tmp_path / "nope.jsonl") == []


# --------------------------------------------------------------- the prompt


def make_repo(tmp_path: Path) -> Path:
    for sub in ("archive", "log", "feedback", "prompts", "corpus"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / "georgia-soul.md").write_text("# Georgia\nI am Georgia.\n")
    (tmp_path / "facts.json").write_text(json.dumps({"name": "Jeff Clark", "projects": []}))
    return tmp_path


def test_no_selection_argument_means_no_corpus_in_the_prompt_at_all(tmp_path):
    """Every pre-corpus caller keeps working, and text-only stays the default."""
    out = assemble_prompt(DAY, repo_root=make_repo(tmp_path))
    assert "[taste]" not in out
    assert "<taste>" not in out


def test_frames_shown_puts_both_the_history_and_the_task_in(tmp_path):
    out = assemble_prompt(DAY, repo_root=make_repo(tmp_path), shown_frame_ids=SHOWN)
    assert DAY_1_TASTE_SENTINEL in out
    assert "<taste>...</taste>" in out


def test_a_dark_day_never_asks_for_a_taste_block(tmp_path):
    """Validation drops entries about frames she wasn't shown, so asking anyway
    would reliably generate warnings for output we requested."""
    out = assemble_prompt(DAY, repo_root=make_repo(tmp_path), shown_frame_ids=[])
    assert CORPUS_DARK_SENTINEL in out
    assert "<taste>...</taste>" not in out


def test_prior_verdicts_for_every_anchor_shown_today_reach_the_prompt(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "corpus" / "preferences.jsonl").write_text(
        "\n".join(json.dumps(pref(f, "2026-05-01", verdict=f"verdict for {f}")) for f in SHOWN)
    )
    out = assemble_prompt(DAY, repo_root=repo, shown_frame_ids=SHOWN)
    for f in SHOWN:
        assert f"verdict for {f}" in out


# ------------------------------------------------------------- validate_taste


def line(**over):
    body = {"frame_id": SHOWN[0], "verdict": "the gap is better than the form",
            "compared_to": None, "confidence": 3}
    body.update(over)
    return json.dumps(body)


def three_good():
    return "\n".join(line(frame_id=f) for f in SHOWN)


def test_a_well_formed_block_yields_appendable_entries():
    entries, warnings = validate_taste(three_good(), SHOWN, TODAY)
    assert len(entries) == 3
    assert warnings == []
    assert all(e["date"] == TODAY for e in entries)


def test_a_missing_block_is_a_warning_not_a_failure():
    entries, warnings = validate_taste("", SHOWN, TODAY)
    assert entries == []
    assert any("No <taste> block" in w for w in warnings)


def test_one_malformed_line_is_skipped_and_the_others_land():
    raw = line(frame_id=SHOWN[0]) + "\n{ not json\n" + line(frame_id=SHOWN[1])
    entries, warnings = validate_taste(raw, SHOWN, TODAY)
    assert [e["frame_id"] for e in entries] == [SHOWN[0], SHOWN[1]]
    assert any("not valid JSON" in w for w in warnings)


def test_a_verdict_about_a_frame_she_was_not_shown_is_dropped():
    raw = three_good() + "\n" + line(frame_id="never-shown-t9")
    entries, warnings = validate_taste(raw, SHOWN, TODAY)
    assert all(e["frame_id"] != "never-shown-t9" for e in entries)
    assert any("not shown today" in w for w in warnings)


def test_fewer_than_three_entries_warns_but_keeps_them():
    entries, warnings = validate_taste(line(), SHOWN, TODAY)
    assert len(entries) == 1
    assert any("at least 3" in w for w in warnings)


def test_more_than_eight_entries_keeps_the_first_eight():
    raw = "\n".join(line(frame_id=SHOWN[i % 3], verdict=f"v{i}") for i in range(11))
    entries, warnings = validate_taste(raw, SHOWN, TODAY)
    assert len(entries) == 8
    assert any("keeping the first 8" in w for w in warnings)
    assert [e["verdict"] for e in entries] == [f"v{i}" for i in range(8)]


@pytest.mark.parametrize("bad", [0, 6, -1, 99])
def test_confidence_outside_one_to_five_drops_the_entry(bad):
    raw = three_good() + "\n" + line(frame_id=SHOWN[0], confidence=bad, verdict="bad conf")
    entries, warnings = validate_taste(raw, SHOWN, TODAY)
    assert all(e["verdict"] != "bad conf" for e in entries)
    assert any("outside 1-5" in w for w in warnings)


@pytest.mark.parametrize("bad", ["3", 3.5, None, True])
def test_a_non_integer_confidence_drops_the_entry(bad):
    entries, warnings = validate_taste(line(confidence=bad), SHOWN, TODAY)
    assert entries == []
    assert any("non-integer confidence" in w for w in warnings)


def test_an_empty_verdict_is_dropped():
    entries, warnings = validate_taste(line(verdict="   "), SHOWN, TODAY)
    assert entries == []
    assert any("empty verdict" in w for w in warnings)


def test_a_comparison_is_kept_verbatim():
    entries, _ = validate_taste(line(compared_to=SHOWN[1]), SHOWN, TODAY)
    assert entries[0]["compared_to"] == SHOWN[1]


# ---------------------------------------------------------- the append is safe


def test_appending_never_rewrites_or_reorders_what_is_already_there(tmp_path):
    repo = tmp_path
    (repo / "corpus").mkdir()
    path = repo / "corpus" / "preferences.jsonl"
    path.write_text(json.dumps(pref("old-t1", "2026-01-01")) + "\n")
    before = path.read_text()

    wo.append_preferences([pref("new-t1", TODAY)], repo)
    after = path.read_text()
    assert after.startswith(before)
    assert after.count("\n") == 2


def test_appending_nothing_does_not_create_the_file(tmp_path):
    wo.append_preferences([], tmp_path)
    assert not (tmp_path / "corpus" / "preferences.jsonl").exists()


def test_the_prompt_archive_records_what_was_shown():
    text = wo.prompt_archive_text("PROMPT", SHOWN, manifest_version=1, shape_show_ids=["bd-2014"])
    assert "## Corpus shown" in text
    for f in SHOWN:
        assert f"- {f}" in text
    assert "Manifest version: 1" in text
    assert "bd-2014" in text


def test_a_text_only_day_archives_exactly_the_prompt():
    assert wo.prompt_archive_text("PROMPT", None) == "PROMPT"
    assert wo.prompt_archive_text("PROMPT", []) == "PROMPT"


# ------------------------------------------------------------ orchestration


FACTS = {"name": "Jeff Clark", "email": "jeff@clarkle.com", "linkedin_url": "https://li", "projects": []}


def _html():
    return (
        "<!DOCTYPE html><html><body><h1>Jeff Clark</h1>"
        + "<p>Jeff Clark is the person. </p>" * 30
        + "<p>jeff@clarkle.com</p><a href='https://li'>x</a>"
        + "<p>filler</p>" * 80
        + "</body></html>"
    )


def _diary():
    return f"---\ndate: {TODAY}\nimportance: 2\n---\n\nA day happened and I noticed it.\n"


def _patch(monkeypatch, selection, taste=""):
    sink: list = []
    monkeypatch.setattr(run_module.corpus_select, "selection_for_date", lambda *a, **k: selection)
    monkeypatch.setattr(
        run_module, "assemble_prompt",
        lambda run_date, repo_root=None, shown_frame_ids=None: f"PROMPT[{len(shown_frame_ids or [])}]",
    )
    monkeypatch.setattr(run_module, "record_stats", lambda *a, **k: sink.append((a, k)))
    monkeypatch.setattr(run_module, "write_outputs", MagicMock())
    monkeypatch.setattr(
        run_module, "call_model",
        lambda request: ModelResult(_html(), _diary(), 1000, 2000, taste=taste),
    )
    return sink


def _selection(frame_ids):
    return corpus_select.CorpusSelection(
        frame_ids=tuple(frame_ids),
        anchor_ids=tuple(frame_ids),
        blocks=tuple(
            b for f in frame_ids for b in (
                {"type": "text", "text": f"[{f}]"},
                {"type": "image", "source": {"type": "file", "file_id": "file_x"}},
            )
        ),
        manifest_version=1,
    )


def test_a_good_taste_block_is_threaded_into_write_outputs(monkeypatch, tmp_path):
    _patch(monkeypatch, _selection(SHOWN), taste=three_good())
    assert run_module.run(TODAY, FACTS, tmp_path, no_commit=True) == 0
    kwargs = run_module.write_outputs.call_args.kwargs
    assert list(kwargs["frame_ids"]) == SHOWN
    assert len(kwargs["taste_entries"]) == 3


def test_a_missing_taste_block_still_ships_the_day(monkeypatch, tmp_path):
    sink = _patch(monkeypatch, _selection(SHOWN), taste="")
    assert run_module.run(TODAY, FACTS, tmp_path, no_commit=True) == 0
    _, kwargs = sink[0]
    assert any("No <taste> block" in w for w in kwargs["corpus_warnings"])


def test_a_dark_day_does_not_complain_about_the_missing_block(monkeypatch, tmp_path):
    """We never asked for one, so its absence is not a warning."""
    sink = _patch(monkeypatch, corpus_select.EMPTY, taste="")
    assert run_module.run(TODAY, FACTS, tmp_path, no_commit=True) == 0
    _, kwargs = sink[0]
    assert kwargs["corpus_warnings"] == []
    assert run_module.write_outputs.call_args.kwargs["taste_entries"] == []


def test_a_dead_file_id_costs_the_corpus_not_the_day(monkeypatch, tmp_path):
    sink = _patch(monkeypatch, _selection(SHOWN), taste="")
    calls: list = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:
            raise APIError(message="file_abc not found", request=MagicMock(), body=None)
        return ModelResult(_html(), _diary(), 1000, 2000, taste="")

    monkeypatch.setattr(run_module, "call_model", flaky)
    assert run_module.run(TODAY, FACTS, tmp_path, no_commit=True) == 0

    assert isinstance(calls[0], list)   # first attempt carried images
    assert isinstance(calls[1], str)    # the retry did not
    _, kwargs = sink[0]
    assert any(w.startswith("corpus_dropped") for w in kwargs["corpus_warnings"])
    assert run_module.write_outputs.call_args.kwargs["frame_ids"] == ()


def test_the_text_only_retry_reassembles_the_prompt_for_a_dark_shelf(monkeypatch, tmp_path):
    """Asking for verdicts on frames she never saw would be guaranteed warnings."""
    _patch(monkeypatch, _selection(SHOWN), taste="")
    calls: list = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:
            raise APIError(message="file gone", request=MagicMock(), body=None)
        return ModelResult(_html(), _diary(), 1000, 2000, taste="")

    monkeypatch.setattr(run_module, "call_model", flaky)
    run_module.run(TODAY, FACTS, tmp_path, no_commit=True)
    assert calls[1] == "PROMPT[0]"


def test_the_corpus_retry_does_not_spend_a_validation_attempt(monkeypatch, tmp_path):
    sink = _patch(monkeypatch, _selection(SHOWN), taste="")
    calls: list = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:
            raise APIError(message="file gone", request=MagicMock(), body=None)
        return ModelResult(_html(), _diary(), 1000, 2000, taste="")

    monkeypatch.setattr(run_module, "call_model", flaky)
    run_module.run(TODAY, FACTS, tmp_path, no_commit=True)
    args, _ = sink[0]
    assert args[1] == 1  # attempts


def test_a_second_api_error_after_the_corpus_is_gone_still_fails_the_run(monkeypatch, tmp_path):
    _patch(monkeypatch, _selection(SHOWN), taste="")

    def always_bad(request):
        raise APIError(message="down", request=MagicMock(), body=None)

    monkeypatch.setattr(run_module, "call_model", always_bad)
    assert run_module.run(TODAY, FACTS, tmp_path, no_commit=True) == 1


def test_a_validation_retry_keeps_the_corpus_attached(monkeypatch, tmp_path):
    _patch(monkeypatch, _selection(SHOWN), taste="")
    calls: list = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:
            return ModelResult("too short", _diary(), 1, 1)
        return ModelResult(_html(), _diary(), 1000, 2000, taste="")

    monkeypatch.setattr(run_module, "call_model", flaky)
    run_module.run(TODAY, FACTS, tmp_path, no_commit=True)
    assert isinstance(calls[1], list)
    assert any(b["type"] == "image" for b in calls[1])


# --------------------------------------- hostile input that must not cost a day
#
# Everything below is a shape Georgia could emit, or a file could contain, that
# would take the site down if it reached an unguarded `x in some_set`.


@pytest.mark.parametrize("hostile", [
    {"frame_id": ["a", "b"], "verdict": "v", "confidence": 3},
    {"frame_id": {"a": 1}, "verdict": "v", "confidence": 3},
    {"frame_id": 152, "verdict": "v", "confidence": 3},
    {"frame_id": None, "verdict": "v", "confidence": 3},
])
def test_an_unhashable_or_non_string_frame_id_is_dropped_not_raised(hostile):
    entries, warnings = validate_taste(json.dumps(hostile), SHOWN, TODAY)
    assert entries == []
    assert any("not shown today" in w for w in warnings)


def test_a_preference_line_with_a_list_frame_id_cannot_poison_tomorrow(tmp_path, capsys):
    """One bad line would otherwise raise inside assemble_prompt, every day, forever."""
    path = tmp_path / "preferences.jsonl"
    path.write_text(
        json.dumps({"frame_id": ["oops"], "verdict": "v", "date": "2026-08-20"}) + "\n"
        + json.dumps(pref(SHOWN[0], "2026-08-20", verdict="good line")) + "\n"
    )
    entries = load_preferences(path)
    assert [e["frame_id"] for e in entries] == [SHOWN[0]]
    block = build_taste_block(entries, SHOWN, DAY)
    assert "good line" in block


def test_a_non_string_id_in_todays_selection_does_not_raise():
    build_taste_block([pref(SHOWN[0], "2026-08-20")], [SHOWN[0], ["bad"]], DAY)


def test_write_outputs_actually_writes_the_corpus_record(tmp_path):
    """The archive-honesty contract, end to end through the real function."""
    for sub in ("archive", "log", "prompts", "corpus"):
        (tmp_path / sub).mkdir(parents=True)
    entries = [pref(SHOWN[0], TODAY, verdict="first"), pref(SHOWN[1], TODAY, verdict="second")]

    wo.write_outputs(
        TODAY, "<html><body>x</body></html>", _diary(), "THE PROMPT",
        no_commit=True, repo_root=tmp_path,
        frame_ids=SHOWN, manifest_version=1, shape_show_ids=["bd-2014"],
        taste_entries=entries,
    )

    archived = (tmp_path / "prompts" / f"{TODAY}.md").read_text()
    assert "THE PROMPT" in archived
    assert "## Corpus shown" in archived
    for f in SHOWN:
        assert f"- {f}" in archived
    assert "Manifest version: 1" in archived

    written = [json.loads(l) for l in (tmp_path / "corpus" / "preferences.jsonl").read_text().splitlines()]
    assert [e["verdict"] for e in written] == ["first", "second"]


def test_a_text_only_day_writes_no_corpus_section_and_no_preferences(tmp_path):
    for sub in ("archive", "log", "prompts"):
        (tmp_path / sub).mkdir(parents=True)
    wo.write_outputs(
        TODAY, "<html><body>x</body></html>", _diary(), "THE PROMPT",
        no_commit=True, repo_root=tmp_path,
    )
    assert (tmp_path / "prompts" / f"{TODAY}.md").read_text() == "THE PROMPT"
    assert not (tmp_path / "corpus" / "preferences.jsonl").exists()


def test_a_malformed_corpus_file_cannot_stop_a_run(tmp_path):
    """The readers are hardened, so this passes on its own merits."""
    from scripts.record_stats import record_stats
    import time as _time

    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "verify.json").write_text("[]")            # not an object
    (tmp_path / "corpus" / "consistency.jsonl").write_text("{ nope\n")
    record_stats(TODAY, 1, [], 0, True, _time.monotonic(), repo_root=tmp_path)
    assert (tmp_path / "stats.jsonl").exists()
    assert (tmp_path / "stats.html").exists()


def test_a_stats_page_that_raises_anyway_still_cannot_stop_a_run(monkeypatch, tmp_path):
    """The outer guard, pinned independently of the hardened readers.

    record_stats runs on every exit path — including the success path, *after*
    run_georgia has set committed=True and *before* write_outputs runs. Anything
    that escapes build_stats_page there means the stats line claims a site shipped
    that never did. Hardening each reader is the first line; this is the second,
    and it has to be tested separately or a future reader can quietly reintroduce
    the hazard.
    """
    import scripts.record_stats as rs
    import time as _time

    def explode(**kwargs):
        raise RuntimeError("a future corpus file the stats page cannot read")

    monkeypatch.setattr(rs, "build_stats_page", explode)
    rs.record_stats(TODAY, 1, [], 0, True, _time.monotonic(), repo_root=tmp_path)
    assert (tmp_path / "stats.jsonl").exists()


def test_the_corpus_drop_retry_keeps_an_earlier_validation_hint(monkeypatch, tmp_path):
    """Rebuilding the prompt for a dark shelf must not lose what attempt 1 learned.

    Compound but reachable: attempt 1 fails validation, the retry carries the hint,
    and *that* call is the one that hits a dead file_id. The reasons are about the
    site and the diary, so they stay valid once the corpus is gone.
    """
    hints: list[str] = []
    monkeypatch.setattr(run_module.corpus_select, "selection_for_date", lambda *a, **k: _selection(SHOWN))
    monkeypatch.setattr(
        run_module, "assemble_prompt",
        lambda run_date, repo_root=None, shown_frame_ids=None: f"PROMPT[{len(shown_frame_ids or [])}]",
    )
    monkeypatch.setattr(run_module, "record_stats", lambda *a, **k: None)
    monkeypatch.setattr(run_module, "write_outputs", MagicMock())

    calls: list = []

    def flaky(request):
        calls.append(request)
        text = request if isinstance(request, str) else request[-1]["text"]
        hints.append(text)
        if len(calls) == 1:
            return ModelResult("too short", _diary(), 1, 1)      # fails validation
        if len(calls) == 2:
            raise APIError(message="file gone", request=MagicMock(), body=None)
        return ModelResult(_html(), _diary(), 1000, 2000, taste="")

    monkeypatch.setattr(run_module, "call_model", flaky)
    assert run_module.run(TODAY, FACTS, tmp_path, no_commit=True) == 0

    assert len(calls) == 3
    assert "[validation-failure]" in hints[1], "attempt 2 should carry the hint"
    assert "[validation-failure]" in hints[2], "the text-only retry dropped the hint"
    assert hints[2].startswith("PROMPT[0]"), "the retry should still describe a dark shelf"
