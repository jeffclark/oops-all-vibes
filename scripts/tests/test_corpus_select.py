"""Tests for scripts/corpus/select.py — story_016's daily selection.

Pure data in, pure data out. The only thing faked here is the manifest.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

from scripts.corpus import select

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DAY = date(2026, 8, 25)


def manifest(shows: int = 19, keepers: int = 10, anchors: int = 10) -> dict:
    frames = []
    n = 0
    for s in range(shows):
        show_id = f"show-{s:02d}"
        for rank in range(1, keepers + 1):
            n += 1
            frames.append({
                "frame_id": f"{show_id}-t{rank * 8}",
                "file_id": f"file_{n:04d}",
                "show_id": show_id,
                "corps": show_id.upper(),
                "year": 2014,
                "t": rank * 8,
                "url": f"https://www.youtube.com/watch?v={show_id}",
                "axis_tags": [f"org:{show_id}"],
                "role": "anchor" if (rank == 1 and s < anchors) else "rotating",
                "curation_rank": rank,
            })
    return {
        "version": 1,
        "published_at": "2026-08-25",
        "frames": frames,
        "shapes": [
            {"show_id": f"show-{s:02d}", "file_id": f"shape_{s:04d}"} for s in range(shows)
        ],
    }


# ------------------------------------------------------------------ the shape


def test_a_normal_day_is_ten_anchors_six_rotating_four_shapes():
    sel = select.select_for_date(DAY, manifest())
    assert len(sel.anchor_ids) == 10
    assert len(sel.rotating_ids) == 6
    assert len(sel.shape_show_ids) == 4
    assert sel.image_count == 20


def test_every_anchor_is_shown_every_day():
    m = manifest()
    anchors = {f["frame_id"] for f in m["frames"] if f["role"] == "anchor"}
    for day in (date(2026, 8, 25), date(2026, 9, 14), date(2027, 3, 2)):
        sel = select.select_for_date(day, m)
        assert anchors <= set(sel.frame_ids)


def test_the_image_cap_is_never_exceeded():
    sel = select.select_for_date(DAY, manifest())
    assert sel.image_count <= select.MAX_IMAGE_BLOCKS


def test_a_small_corpus_with_fewer_than_ten_anchors_still_selects():
    """story_015 deliberately permits this; asserting 10 here would break it."""
    m = manifest(shows=1, keepers=4, anchors=1)
    m["frames"][0]["role"] = "anchor"
    for f in m["frames"][1:]:
        f["role"] = "anchor"
    sel = select.select_for_date(DAY, m)
    assert len(sel.anchor_ids) == 4
    assert sel.shown


def test_shapes_only_for_shows_in_todays_rotating_set():
    sel = select.select_for_date(DAY, manifest())
    rotating_shows = {fid.rsplit("-t", 1)[0] for fid in sel.rotating_ids}
    assert set(sel.shape_show_ids) <= rotating_shows
    assert len(sel.shape_show_ids) <= select.MAX_SHAPES_PER_DAY


def test_a_show_with_no_shape_entry_is_simply_skipped():
    m = manifest()
    m["shapes"] = []
    sel = select.select_for_date(DAY, m)
    assert sel.shape_show_ids == ()
    assert sel.image_count == 16


# --------------------------------------------------------------- determinism


def test_the_same_date_selects_the_same_thing_twice():
    m = manifest()
    assert select.select_for_date(DAY, m) == select.select_for_date(DAY, m)


def test_different_dates_rotate():
    m = manifest()
    a = select.select_for_date(date(2026, 8, 25), m).rotating_ids
    b = select.select_for_date(date(2026, 8, 26), m).rotating_ids
    assert a != b


def test_rotation_does_not_depend_on_random_module_state():
    """The key is a pure hash, so churning the global RNG changes nothing.

    A replay of a past date has to reproduce exactly what she saw, on any machine
    and in any process — that rules out `random.sample`, whose stream depends on
    global seeding and on the interpreter's implementation.
    """
    import random

    m = manifest()
    once = select.select_for_date(DAY, m).rotating_ids
    random.seed(999)
    [random.random() for _ in range(50)]
    assert select.select_for_date(DAY, m).rotating_ids == once


def test_rotation_covers_the_pool_over_time():
    """A frame should come back sometimes, not never and not constantly."""
    m = manifest()
    seen: set[str] = set()
    start = date(2026, 8, 25).toordinal()
    for i in range(60):
        seen |= set(select.select_for_date(date.fromordinal(start + i), m).rotating_ids)
    pool = {f["frame_id"] for f in m["frames"] if f["role"] != "anchor"}
    # 60 days x 6 slots against a 180-frame pool: broad coverage, no full sweep
    assert 100 < len(seen) < len(pool)


# ------------------------------------------------------------------- the cap


def test_shapes_are_dropped_before_rotating_frames():
    m = manifest(shows=19, keepers=10, anchors=16)  # 16 anchors -> over the cap
    sel = select.select_for_date(DAY, m)
    assert len(sel.anchor_ids) == 16
    assert sel.shape_show_ids == ()
    assert sel.image_count <= select.MAX_IMAGE_BLOCKS


def _pinned_manifest(anchors: int, rotating_ranks):
    """A manifest with an exactly-known rotating pool, so the drop order is testable."""
    frames = [
        {"frame_id": f"anchor-{i:02d}-t8", "file_id": f"file_a{i}", "show_id": f"anchor-{i:02d}",
         "corps": "A", "year": 2014, "t": 8, "url": "u", "axis_tags": [],
         "role": "anchor", "curation_rank": 1}
        for i in range(anchors)
    ]
    frames += [
        {"frame_id": f"rot-{r:02d}-t{r}", "file_id": f"file_r{r}", "show_id": f"rot-{r:02d}",
         "corps": "R", "year": 2014, "t": r, "url": "u", "axis_tags": [],
         "role": "rotating", "curation_rank": r}
        for r in rotating_ranks
    ]
    return {"version": 1, "frames": frames, "shapes": []}


def test_lowest_ranked_rotating_frames_go_before_higher_ranked_ones():
    """Reversing the drop order in select.py must fail this test.

    The whole rotating pool is exactly six frames with ranks 2..7, and 16 anchors
    leave room for four of them. The four that survive must be her four best —
    ranks 2, 3, 4, 5 — not her four worst.
    """
    m = _pinned_manifest(anchors=16, rotating_ranks=[2, 3, 4, 5, 6, 7])
    sel = select.select_for_date(DAY, m)
    by_id = {f["frame_id"]: f for f in m["frames"]}
    kept = sorted(by_id[fid]["curation_rank"] for fid in sel.rotating_ids)
    assert kept == [2, 3, 4, 5]
    assert sel.image_count == select.MAX_IMAGE_BLOCKS


def test_dropping_one_rotating_frame_drops_the_single_worst():
    # 16 anchors + a five-frame rotating pool is 21 blocks: exactly one too many.
    m = _pinned_manifest(anchors=16, rotating_ranks=[2, 3, 4, 5, 9])
    sel = select.select_for_date(DAY, m)
    by_id = {f["frame_id"]: f for f in m["frames"]}
    assert sorted(by_id[fid]["curation_rank"] for fid in sel.rotating_ids) == [2, 3, 4, 5]


def test_an_anchor_is_never_dropped():
    """19 anchors leaves one image slot: the shapes and five of six rotating go,
    and every anchor survives."""
    m = manifest(shows=19, keepers=10, anchors=19)
    sel = select.select_for_date(DAY, m)
    assert len(sel.anchor_ids) == 19
    assert len(sel.rotating_ids) == 1
    assert sel.shape_show_ids == ()
    assert sel.image_count == 20


def test_too_many_anchors_to_fit_is_caught_rather_than_sent():
    """Anchors are never dropped, so an over-cap manifest must fail loudly here —
    where selection_for_date turns it into a text-only day — not at the API."""
    m = manifest(shows=25, keepers=10, anchors=25)
    with pytest.raises(AssertionError, match="over the 20"):
        select.select_for_date(DAY, m)


def test_that_failure_still_ships_the_day(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest(shows=25, keepers=10, anchors=25)))
    assert select.selection_for_date(DAY, path) is select.EMPTY


# ---------------------------------------------------------------- fail open


def test_a_missing_manifest_is_an_empty_selection(tmp_path, capsys):
    assert select.selection_for_date(DAY, tmp_path / "nope.json") is select.EMPTY
    assert "no manifest" in capsys.readouterr().err


def test_malformed_manifest_json_is_an_empty_selection(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text("{ not json")
    assert select.selection_for_date(DAY, path) is select.EMPTY
    assert "unreadable" in capsys.readouterr().err


def test_an_empty_manifest_is_an_empty_selection(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1, "frames": []}))
    assert select.selection_for_date(DAY, path) is select.EMPTY


def test_frames_without_a_file_id_are_skipped_not_sent():
    m = manifest(shows=1, keepers=10, anchors=1)
    m["frames"][0]["file_id"] = ""
    sel = select.select_for_date(DAY, m)
    assert all(b["source"]["file_id"] for b in sel.blocks if b["type"] == "image")


# ------------------------------------------------------------- request shape


def test_images_come_before_the_text_prompt():
    sel = select.select_for_date(DAY, manifest())
    content = select.build_content(sel, "THE PROMPT")
    assert isinstance(content, list)
    last = content[-1]
    assert last == {"type": "text", "text": "THE PROMPT"}
    image_positions = [i for i, b in enumerate(content) if b["type"] == "image"]
    assert max(image_positions) < len(content) - 1


def test_no_corpus_means_the_request_is_still_a_plain_string():
    assert select.build_content(select.EMPTY, "THE PROMPT") == "THE PROMPT"


def test_each_frame_is_labelled_with_its_id_so_she_can_refer_to_it():
    sel = select.select_for_date(DAY, manifest())
    content = list(sel.blocks)
    for i, block in enumerate(content):
        if block["type"] == "image" and "shape:" not in content[i - 1]["text"]:
            assert re.fullmatch(r"\[show-\d\d-t\d+\]", content[i - 1]["text"])


def test_frames_are_referenced_by_file_id_not_by_bytes():
    sel = select.select_for_date(DAY, manifest())
    for block in sel.blocks:
        if block["type"] == "image":
            assert block["source"]["type"] == "file"
            assert "data" not in block["source"]


def test_the_frame_id_list_matches_the_image_blocks():
    sel = select.select_for_date(DAY, manifest())
    frame_images = sum(
        1 for b in sel.blocks if b["type"] == "image"
    ) - len(sel.shape_show_ids)
    assert frame_images == len(sel.frame_ids)


# -------------------------------------------------------------------- hygiene


def test_select_imports_nothing_from_requirements_corpus():
    heavy = {"librosa", "matplotlib", "numpy", "PIL", "yt_dlp"}
    src = (REPO_ROOT / "scripts" / "corpus" / "select.py").read_text()
    for mod in heavy:
        assert not re.search(rf"^\s*(import|from)\s+{mod}\b", src, re.M), mod


def test_the_daily_pipeline_never_pulls_in_a_corpus_dependency():
    """run_georgia imports select; select must not drag the heavy half in with it."""
    import subprocess

    code = (
        "import sys; sys.path.insert(0, r'%s');\n"
        "import scripts.run_georgia\n"
        "bad = [m for m in ('librosa','matplotlib','numpy','PIL','yt_dlp') if m in sys.modules]\n"
        "print(','.join(bad))\n" % REPO_ROOT
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"daily pipeline imported {out.stdout.strip()}"


def test_a_shows_odds_of_getting_its_shape_plot_do_not_depend_on_its_name():
    """Six rotating frames, at most four shape plots — so which four is a real choice.

    Taking them in display order (show_id, curation_rank) rather than in rotation
    order makes the audio shape an alphabetical privilege. Measured over 200 days
    against a 19-show manifest, drawing from rotation order gives every show a
    P(shape | rotating) between 0.60 and 0.77; drawing from display order gives
    show-01 0.94 and show-18 0.08, a clean monotone gradient down the alphabet.
    The ratio is the discriminator: ~1.3 when it is right, ~12 when it is not.
    """
    m = manifest()
    rotated: dict[str, int] = {}
    shaped: dict[str, int] = {}
    start = date(2026, 8, 25).toordinal()
    for i in range(200):
        sel = select.select_for_date(date.fromordinal(start + i), m)
        for fid in sel.rotating_ids:
            show = fid.rsplit("-t", 1)[0]
            rotated[show] = rotated.get(show, 0) + 1
        for show in sel.shape_show_ids:
            shaped[show] = shaped.get(show, 0) + 1

    assert len(rotated) == 19, "every show should reach the rotation over 200 days"
    rates = {s: shaped.get(s, 0) / n for s, n in rotated.items()}
    assert min(rates.values()) > 0.4, f"a show is being starved of its shape plot: {rates}"
    assert max(rates.values()) / min(rates.values()) < 2.0, (
        f"shape plots are not evenly distributed across shows: {rates}"
    )


def test_shape_shows_still_only_come_from_todays_rotating_set():
    for i in range(30):
        sel = select.select_for_date(date.fromordinal(date(2026, 8, 25).toordinal() + i), manifest())
        rotating_shows = {fid.rsplit("-t", 1)[0] for fid in sel.rotating_ids}
        assert set(sel.shape_show_ids) <= rotating_shows


def test_publish_verify_only_never_pulls_in_a_corpus_dependency():
    """The daily workflow runs this step with requirements.txt installed and nothing else."""
    import subprocess

    code = (
        "import sys; sys.path.insert(0, r'%s');\n"
        "import scripts.corpus.publish\n"
        "bad = [m for m in ('librosa','matplotlib','numpy','PIL','yt_dlp') if m in sys.modules]\n"
        "print(','.join(bad))\n" % REPO_ROOT
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"publish --verify-only would need {out.stdout.strip()}"
