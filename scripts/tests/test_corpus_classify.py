"""Tests for scripts/corpus/classify.py — story_013a's vision fallback.

No API calls: the client is faked at the `messages.create` boundary and
everything on this side of it — batching, coverage checking, the partition
handoff back into ingest — runs for real.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.corpus import classify, framing, ingest
from scripts.corpus.ingest import IngestError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def make_frames(d: Path, times):
    from PIL import Image

    d.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(times):
        Image.new("RGB", (64, 36), (i * 7 % 256, 40, 90)).save(d / f"t{t:05d}.jpg")


def make_show(tmp_path: Path, times, show_id="pr-2003") -> Path:
    show_dir = tmp_path / show_id
    make_frames(show_dir / "frames", times)
    (show_dir / "ingest.json").write_text(
        json.dumps({"show_id": show_id, "frame_times": list(times)})
    )
    return show_dir


class FakeClient:
    """Returns a canned verdict payload per call and records what it was sent."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        body = self.payloads.pop(0)
        text = body if isinstance(body, str) else json.dumps(body)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason="end_turn",
        )


def all_field(times, field=True):
    return {"verdicts": [{"t": t, "field": field, "reason": "ok"} for t in times]}


# ------------------------------------------------------------------ batching


def test_every_candidate_gets_a_verdict(tmp_path):
    times = list(range(0, 8 * 45, 8))  # 45 frames -> 3 batches of 20/20/5
    show_dir = make_show(tmp_path, times)
    batches = [times[i:i + 20] for i in range(0, len(times), 20)]
    client = FakeClient([all_field(b) for b in batches])

    payload = classify.classify_show("pr-2003", out_root=tmp_path, client=client)

    assert len(client.calls) == 3
    assert [v["t"] for v in payload["verdicts"]] == times
    assert payload["model"] == classify.MODEL


def test_batches_never_exceed_the_batch_size(tmp_path):
    times = list(range(0, 8 * 45, 8))
    show_dir = make_show(tmp_path, times)
    batches = [times[i:i + 20] for i in range(0, len(times), 20)]
    client = FakeClient([all_field(b) for b in batches])
    classify.classify_show("pr-2003", out_root=tmp_path, client=client)

    for call in client.calls:
        images = [b for b in call["messages"][0]["content"] if b["type"] == "image"]
        assert len(images) <= classify.BATCH_SIZE


def test_each_image_is_preceded_by_its_timestamp_label(tmp_path):
    times = [0, 8, 16]
    make_show(tmp_path, times)
    client = FakeClient([all_field(times)])
    classify.classify_show("pr-2003", out_root=tmp_path, client=client)

    content = client.calls[0]["messages"][0]["content"]
    labels = [b["text"] for b in content if b["type"] == "text"]
    assert labels[:3] == ["Frame t=0", "Frame t=8", "Frame t=16"]


def test_the_question_asks_about_the_camera_and_disclaims_quality(tmp_path):
    """A first pass that also rejects loose sets is doing Georgia's job for her.

    The first wording asked whether the shape was "legible", and the model duly
    threw out seven consecutive press-box frames of a deliberately scattered set
    on pr-2003. Those are framing-valid and possibly the most interesting frames
    in the show; declining them is a preference, and preference is story_014's.
    """
    make_show(tmp_path, [0])
    client = FakeClient([all_field([0])])
    classify.classify_show("pr-2003", out_root=tmp_path, client=client)
    asked = client.calls[0]["messages"][0]["content"][-1]["text"]
    system = client.calls[0]["system"]

    assert "elevated" in asked
    assert "scattered" in asked and "mid-transition" in asked
    assert "not your call" in asked
    assert "never comment on whether a formation is good" in system.lower()


def test_a_response_missing_frames_fails_rather_than_writing_a_partial_file(tmp_path):
    times = [0, 8, 16]
    show_dir = make_show(tmp_path, times)
    client = FakeClient([all_field([0, 8])])  # 16 missing

    with pytest.raises(IngestError, match="no verdict for 1 frame"):
        classify.classify_show("pr-2003", out_root=tmp_path, client=client)
    assert not (show_dir / classify.CLASSIFIED_FILENAME).exists()


def test_hallucinated_timestamps_are_dropped(tmp_path):
    times = [0, 8]
    make_show(tmp_path, times)
    payload = {"verdicts": [
        {"t": 0, "field": True, "reason": "a"},
        {"t": 999, "field": True, "reason": "not a real candidate"},
        {"t": 8, "field": False, "reason": "b"},
    ]}
    client = FakeClient([payload])
    got = classify.classify_show("pr-2003", out_root=tmp_path, client=client)
    assert [v["t"] for v in got["verdicts"]] == [0, 8]


def test_missing_frame_on_disk_is_caught_before_any_call(tmp_path):
    show_dir = make_show(tmp_path, [0, 8])
    (show_dir / "frames" / "t00008.jpg").unlink()
    client = FakeClient([])
    with pytest.raises(IngestError, match="not on disk"):
        classify.classify_show("pr-2003", out_root=tmp_path, client=client)
    assert client.calls == []


def test_missing_ingest_json_names_the_fix(tmp_path):
    (tmp_path / "pr-2003").mkdir()
    with pytest.raises(IngestError, match="ingest"):
        classify.classify_show("pr-2003", out_root=tmp_path, client=FakeClient([]))


# --------------------------------------------------------------- idempotence


def test_an_already_classified_show_is_not_reclassified(tmp_path):
    times = [0, 8]
    show_dir = make_show(tmp_path, times)
    classify.classify_show("pr-2003", out_root=tmp_path, client=FakeClient([all_field(times)]))

    second = FakeClient([])
    classify.classify_show("pr-2003", out_root=tmp_path, client=second)
    assert second.calls == []


def test_force_reclassifies(tmp_path):
    times = [0, 8]
    make_show(tmp_path, times)
    classify.classify_show("pr-2003", out_root=tmp_path, client=FakeClient([all_field(times)]))

    second = FakeClient([all_field(times, field=False)])
    got = classify.classify_show("pr-2003", out_root=tmp_path, client=second, force=True)
    assert len(second.calls) == 1
    assert all(v["field"] is False for v in got["verdicts"])


# -------------------------------------------------------------------- no bulk


def test_cli_requires_a_show():
    with pytest.raises(SystemExit) as exc:
        classify.main([])
    assert exc.value.code != 0


# ------------------------------------------------- handoff back into ingest


def test_ingest_prefers_the_classifier_when_present(tmp_path):
    times = [0, 8, 16, 24]
    show_dir = make_show(tmp_path, times)
    frame_paths = sorted((show_dir / "frames").glob("t*.jpg"))
    # Every frame scores as junk under the heuristic, which is the exact
    # situation story_013a exists for.
    scores = {p.stem: 0.01 for p in frame_paths}
    (show_dir / classify.CLASSIFIED_FILENAME).write_text(json.dumps({
        "show_id": "pr-2003",
        "verdicts": [
            {"t": 0, "field": True, "reason": ""},
            {"t": 8, "field": False, "reason": ""},
            {"t": 16, "field": True, "reason": ""},
            {"t": 24, "field": True, "reason": ""},
        ],
    }))

    mechanism, field_paths, other_paths = ingest.partition_candidates(
        show_dir, frame_paths, scores
    )
    assert mechanism == classify.MECHANISM_CLASSIFIER
    assert [ingest._frame_seconds(p) for p in field_paths] == [0, 16, 24]
    assert [ingest._frame_seconds(p) for p in other_paths] == [8]


def test_ingest_falls_back_to_the_heuristic_without_a_verdict_file(tmp_path):
    times = [0, 8]
    show_dir = make_show(tmp_path, times)
    frame_paths = sorted((show_dir / "frames").glob("t*.jpg"))
    scores = {"t00000": 0.40, "t00008": 0.02}

    mechanism, field_paths, other_paths = ingest.partition_candidates(
        show_dir, frame_paths, scores
    )
    assert mechanism == classify.MECHANISM_HEURISTIC
    assert [ingest._frame_seconds(p) for p in field_paths] == [0]
    assert [ingest._frame_seconds(p) for p in other_paths] == [8]


def test_a_stale_verdict_file_is_ignored_rather_than_half_applied(tmp_path):
    """Re-sampling at a denser interval_s must not mix two mechanisms in one show."""
    times = [0, 4, 8]
    show_dir = make_show(tmp_path, times)
    frame_paths = sorted((show_dir / "frames").glob("t*.jpg"))
    (show_dir / classify.CLASSIFIED_FILENAME).write_text(json.dumps({
        "verdicts": [{"t": 0, "field": True, "reason": ""}, {"t": 8, "field": True, "reason": ""}],
    }))
    scores = {p.stem: 0.01 for p in frame_paths}

    mechanism, field_paths, _ = ingest.partition_candidates(show_dir, frame_paths, scores)
    assert mechanism == classify.MECHANISM_HEURISTIC
    assert field_paths == []


def test_a_corrupt_verdict_file_drops_back_to_the_heuristic(tmp_path):
    times = [0, 8]
    show_dir = make_show(tmp_path, times)
    (show_dir / classify.CLASSIFIED_FILENAME).write_text("{not json")
    frame_paths = sorted((show_dir / "frames").glob("t*.jpg"))
    mechanism, _, _ = ingest.partition_candidates(
        show_dir, frame_paths, {p.stem: 0.9 for p in frame_paths}
    )
    assert mechanism == classify.MECHANISM_HEURISTIC


@pytest.mark.parametrize("field_verdict", [True, False])
def test_nothing_is_ever_deleted_under_either_mechanism(tmp_path, field_verdict):
    times = [0, 8, 16]
    show_dir = make_show(tmp_path, times)
    frame_paths = sorted((show_dir / "frames").glob("t*.jpg"))
    (show_dir / classify.CLASSIFIED_FILENAME).write_text(json.dumps({
        "verdicts": [{"t": t, "field": field_verdict, "reason": ""} for t in times],
    }))
    _, field_paths, other_paths = ingest.partition_candidates(
        show_dir, frame_paths, {p.stem: 0.01 for p in frame_paths}
    )
    assert sorted(field_paths + other_paths) == sorted(frame_paths)
    assert all(p.exists() for p in frame_paths)


def test_ingest_json_records_the_mechanism():
    result = ingest.ShowIngest(
        show_id="x", corps="c", year=2000, angle="high", url="u",
        duration_s=600.0, interval_s=8, frame_times=[0], sheets=[],
        split_mechanism=classify.MECHANISM_CLASSIFIER,
    )
    assert result.to_dict()["split_mechanism"] == "classifier"


def test_the_mechanism_defaults_to_the_heuristic():
    result = ingest.ShowIngest(
        show_id="x", corps="c", year=2000, angle="high", url="u",
        duration_s=600.0, interval_s=8, frame_times=[0], sheets=[],
    )
    assert result.to_dict()["split_mechanism"] == classify.MECHANISM_HEURISTIC


# -------------------------------------------------------------------- hygiene


def test_classify_is_never_imported_outside_the_corpus_package():
    offenders = []
    for py in REPO_ROOT.glob("scripts/*.py"):
        if re.search(r"^\s*(import|from).*\bclassify\b", py.read_text(), re.M):
            offenders.append(py.name)
    assert not offenders, offenders


def test_a_corrupt_verdict_file_is_an_actionable_message_not_a_traceback(tmp_path):
    show_dir = make_show(tmp_path, [0, 8])
    (show_dir / classify.CLASSIFIED_FILENAME).write_text("{ truncated")
    with pytest.raises(IngestError, match="not valid JSON"):
        classify.classify_show("pr-2003", out_root=tmp_path, client=FakeClient([]))


def test_that_corrupt_file_exits_two_rather_than_crashing(tmp_path, capsys):
    show_dir = make_show(tmp_path, [0, 8])
    (show_dir / classify.CLASSIFIED_FILENAME).write_text("{ truncated")
    assert classify.main(["--show", "pr-2003", "--out", str(tmp_path)]) == 2
    assert "--force" in capsys.readouterr().err
