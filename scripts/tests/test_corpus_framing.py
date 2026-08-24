"""Tests for the field-shot scorer.

Honest scope: these pin the scorer's behaviour on synthetic colour fields and the
mechanics of partitioning. They do NOT establish that FIELD_THRESHOLD is correctly
tuned for real broadcast footage — that needs measuring against actual sheets, which
is why nothing in the pipeline deletes a frame based on this score.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.corpus import framing


def img(tmp_path: Path, name: str, colour, size=(320, 180)) -> Path:
    from PIL import Image

    p = tmp_path / f"{name}.jpg"
    Image.new("RGB", size, colour).save(p)
    return p


TURF = (60, 150, 60)
SKIN = (200, 150, 120)
UNIFORM = (30, 70, 40)   # dark green serge — deliberately near the hue band
CROWD = (120, 118, 125)


def test_turf_scores_high(tmp_path):
    assert framing.field_score(img(tmp_path, "turf", TURF)) > 0.9


@pytest.mark.parametrize("colour", [SKIN, CROWD, (10, 10, 10), (240, 240, 240)])
def test_non_field_scores_low(tmp_path, colour):
    assert framing.field_score(img(tmp_path, "x", colour)) < 0.2


def test_half_field_scores_near_half(tmp_path):
    from PIL import Image

    im = Image.new("RGB", (320, 180), SKIN)
    im.paste(Image.new("RGB", (320, 90), TURF), (0, 0))
    p = tmp_path / "half.jpg"
    im.save(p)
    assert 0.4 < framing.field_score(p) < 0.6


def test_dark_uniform_green_is_not_counted_as_turf(tmp_path):
    """A hornline filling the frame is green-ish; the value floor must reject it."""
    assert framing.field_score(img(tmp_path, "uni", UNIFORM)) < framing.FIELD_THRESHOLD


def test_is_field_uses_threshold():
    assert framing.is_field(0.9)
    assert not framing.is_field(0.01)
    assert framing.is_field(0.2, threshold=0.1)


def test_partition_preserves_chronological_order(tmp_path):
    paths = [tmp_path / f"t{t:05d}.jpg" for t in (0, 6, 12, 18, 24)]
    scores = {"t00000": 0.9, "t00006": 0.0, "t00012": 0.8, "t00018": 0.1, "t00024": 0.95}
    field, other = framing.partition(paths, scores)
    assert [p.stem for p in field] == ["t00000", "t00012", "t00024"]
    assert [p.stem for p in other] == ["t00006", "t00018"]


def test_partition_is_total(tmp_path):
    paths = [tmp_path / f"t{t:05d}.jpg" for t in range(0, 60, 6)]
    scores = {p.stem: (0.9 if i % 2 else 0.0) for i, p in enumerate(paths)}
    field, other = framing.partition(paths, scores)
    assert len(field) + len(other) == len(paths)
    assert set(field).isdisjoint(other)


def test_unscored_frame_falls_to_other(tmp_path):
    paths = [tmp_path / "t00000.jpg"]
    field, other = framing.partition(paths, {})
    assert not field and other == paths


def test_score_frames_keys_on_stem(tmp_path):
    ps = [img(tmp_path, "t00000", TURF), img(tmp_path, "t00006", SKIN)]
    scores = framing.score_frames(ps)
    assert set(scores) == {"t00000", "t00006"}
    assert scores["t00000"] > scores["t00006"]


def test_histogram_counts_every_frame():
    scores = {f"t{i}": i / 20 for i in range(21)}
    h = framing.histogram(scores)
    assert sum(c for _, _, c in h) == len(scores)


# ---------------------------------------------- threshold placement, from real data

# Scores measured off labelled Madison 1995 contact sheets. The point of these is
# that the threshold sits in the empty gap between the junk band and the drill band,
# not inside either one.
MEASURED_DRILL = [0.27, 0.29, 0.31, 0.32, 0.34, 0.35, 0.37, 0.39, 0.42, 0.47, 0.54]
MEASURED_JUNK = [0.00, 0.03, 0.06, 0.08, 0.09, 0.10, 0.11, 0.13, 0.17, 0.21, 0.24]


@pytest.mark.parametrize("score", MEASURED_DRILL)
def test_measured_drill_frames_are_kept(score):
    assert framing.is_field(score), f"{score} is a real formation and must survive"


@pytest.mark.parametrize("score", MEASURED_JUNK)
def test_measured_junk_frames_are_dropped(score):
    assert not framing.is_field(score)


def test_threshold_sits_in_the_gap_between_the_bands():
    assert max(MEASURED_JUNK) < framing.FIELD_THRESHOLD <= min(MEASURED_DRILL)


def test_dancer_on_turf_is_a_known_accepted_false_positive():
    """A tight shot of one person on grass scores ~0.63. Documented, not fixed."""
    assert framing.is_field(0.63)
