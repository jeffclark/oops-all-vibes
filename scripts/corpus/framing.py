"""Score how much of a frame is playing field, to separate drill shots from close-ups.

Why this exists: story_013 originally said "no automatic detection of which frames
are which — curation is already the filter." That held while close-ups were a
modest minority. Madison Scouts 1995 came back roughly one-third usable, and at
that ratio the filtering stops being a taste act: declining a close-up of a boot
is a category judgement, not a preference, and spending Georgia's attention on
135 of them to reach 65 real candidates is waste.

The signal is domain-specific and blunt on purpose. A high-angle drill shot is
mostly turf with small figures on it; a close-up is skin, uniform, and crowd.
Green fraction separates those without needing to understand anything about the
image.

Known failure mode: a show that tarps over most of the field — common in modern
productions — will read as low-field even from the press box. That is why nothing
here deletes a frame. Scores are recorded and used to *order and partition*
contact sheets so a human can check the split before it is trusted.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

# PIL packs HSV into 0-255. Turf sits around 90-150 degrees of hue; the band is
# kept wide because broadcast footage, stadium lighting and 1990s tape all shift
# it. Saturation and value floors drop grey crowd and dark night sky.
HUE_LO, HUE_HI = 45, 115
SAT_MIN = 60

# The value floor is doing more work than it looks. Dark green uniform serge sits
# at roughly hue 135 deg / value 70 — inside the turf hue band — so a hornline
# filling the frame scored as "field" at a floor of 40. Madison Scouts wear green,
# which is precisely the show this scorer exists for. Lit turf runs near value 150,
# so 100 separates them. Unvalidated against real footage; if genuinely dim tape
# starts reading as all-close-up, this is the first constant to revisit.
VAL_MIN = 100

# Above this fraction of green pixels a frame is treated as a field shot.
#
# Measured against real Madison 1995 sheets rather than guessed. Scoring every
# labelled cell on one field sheet and one other sheet gave three bands:
#
#     0.00 - 0.24   crowd, pit close-ups, faces, a boot on a podium
#     0.27 - 0.54   essentially all real drill, wide and mid
#     0.63          a single dancer standing on turf
#
# The first cut was 0.35, which sat in the middle of the drill band and split it in
# half — the widest, most useful formations score LOW, because a true wide shot of a
# stadium necessarily includes a thick band of crowd. 0.25 lands in the gap.
#
# The 0.63 dancer is a known false positive and no threshold fixes it: a tight shot
# of one person on grass is nearly all green. It is a handful of frames per show and
# curation rejects them, which is the kind of judgement curation should be making.
# If a show ever breaks this differently — a heavily tarped modern field reading as
# all-close-up is the likely case — replace this scorer with a vision classifier
# rather than chasing the constant further.
FIELD_THRESHOLD = 0.25


def field_score(path: Path) -> float:
    """Fraction of the frame that reads as playing surface. 0.0-1.0."""
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        # Downscale first: this is a bulk statistic, not a detail measurement, and
        # a whole show's worth of full-size frames is needlessly slow.
        small = im.convert("RGB").resize((160, 90), Image.BILINEAR).convert("HSV")
        arr = np.asarray(small, dtype=np.int16)

    h, s, v = arr[..., 0], arr[..., 1], arr[..., 2]
    green = (h >= HUE_LO) & (h <= HUE_HI) & (s >= SAT_MIN) & (v >= VAL_MIN)
    return float(green.mean())


def score_frames(paths: Sequence[Path]) -> dict[str, float]:
    """Map frame filename stem -> field score."""
    return {p.stem: round(field_score(p), 4) for p in paths}


def is_field(score: float, threshold: float = FIELD_THRESHOLD) -> bool:
    return score >= threshold


def partition(
    paths: Sequence[Path],
    scores: dict[str, float],
    threshold: float = FIELD_THRESHOLD,
) -> tuple[list[Path], list[Path]]:
    """Split into (field, other), each preserving chronological order.

    Order is preserved rather than sorted by score: a contact sheet is read as a
    show unfolding, and shuffling it into a ranking would destroy the one thing a
    sequence of stills still carries, which is what happened next.
    """
    field = [p for p in paths if is_field(scores.get(p.stem, 0.0), threshold)]
    other = [p for p in paths if not is_field(scores.get(p.stem, 0.0), threshold)]
    return field, other


def histogram(scores: dict[str, float], bins: int = 10) -> list[tuple[float, float, int]]:
    """(lo, hi, count) buckets — printed after ingest so the split can be sanity-checked."""
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        n = sum(1 for v in scores.values() if (lo <= v < hi) or (i == bins - 1 and v == 1.0))
        out.append((lo, hi, n))
    return out
