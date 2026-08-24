"""Tests for scripts/corpus/ingest.py.

ffmpeg/yt-dlp are not available in CI and never will be — corpus ingest is an
offline step. So the shelling boundary (`_run`) is faked and everything on this
side of it runs for real: validation, frame naming, plot rendering, sheet
composition, and the ingest.json contract.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.corpus import ingest
from scripts.corpus.ingest import AudioShape, IngestError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def entry(**over):
    base = {
        "show_id": "cav-2004",
        "url": "https://www.youtube.com/watch?v=6TuKic5Lj5k&list=PLabc&index=3",
        "corps": "The Cavaliers",
        "year": 2004,
        "angle": "high",
        "axis_tags": ["era:2000s", "class:dci-world"],
    }
    base.update(over)
    return base


def make_frames(d: Path, times, size=(1024, 576)):
    from PIL import Image

    d.mkdir(parents=True, exist_ok=True)
    out = []
    for i, t in enumerate(times):
        p = d / f"t{t:05d}.jpg"
        Image.new("RGB", size, (i * 7 % 256, 40, 90)).save(p)
        out.append(p)
    return out


# ----------------------------------------------------------------- url + angle


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.youtube.com/watch?v=6TuKic5Lj5k&list=PLabc&index=3",
        "https://www.youtube.com/watch?v=6TuKic5Lj5k&list=RD6TuKic5Lj5k&start_radio=1",
        "https://youtu.be/6TuKic5Lj5k",
        "https://www.youtube.com/watch?v=6TuKic5Lj5k",
    ],
)
def test_canonical_url_strips_playlist_params(raw):
    assert ingest.canonical_url(raw) == "https://www.youtube.com/watch?v=6TuKic5Lj5k"


def test_canonical_url_rejects_nonsense():
    with pytest.raises(IngestError, match="recognisable YouTube URL"):
        ingest.canonical_url("https://example.com/some-show")


def test_sample_interval_differs_by_angle():
    assert ingest.sample_interval("high") == 8
    assert ingest.sample_interval("press-box") == 8
    assert ingest.sample_interval("multi-cam") == 6


def test_field_level_is_rejected_with_a_reason():
    with pytest.raises(IngestError, match="wall of backs"):
        ingest.validate_entry(entry(angle="field-level"))


def test_unknown_angle_is_rejected():
    with pytest.raises(IngestError, match="unknown angle"):
        ingest.validate_entry(entry(angle="drone"))


@pytest.mark.parametrize("bad", [{"year": "2004"}, {"year": True}, {"year": 1200}])
def test_bad_year_rejected(bad):
    with pytest.raises(IngestError, match="year"):
        ingest.validate_entry(entry(**bad))


def test_missing_key_names_the_key():
    e = entry()
    del e["corps"]
    with pytest.raises(IngestError, match="corps"):
        ingest.validate_entry(e)


def test_validate_normalises_url_and_sets_interval():
    got = ingest.validate_entry(entry(angle="multi-cam"))
    assert got["url"] == "https://www.youtube.com/watch?v=6TuKic5Lj5k"
    assert got["interval_s"] == 6


# --------------------------------------------------------------------- sources


def test_real_sources_file_is_valid():
    entries = ingest.load_sources(REPO_ROOT / "corpus" / "sources.json")
    assert len(entries) >= 15
    assert len({e["show_id"] for e in entries}) == len(entries)
    for e in entries:
        assert re.fullmatch(r"https://www\.youtube\.com/watch\?v=[\w-]+", e["url"])
        # Angle sets a default; a per-show interval_s may override it downward.
        assert ingest.MIN_INTERVAL_S <= e["interval_s"] <= ingest.SAMPLE_INTERVAL_S[e["angle"]]


def test_duplicate_show_id_rejected(tmp_path):
    p = tmp_path / "sources.json"
    p.write_text(json.dumps([entry(), entry(url="https://youtu.be/other11")]))
    with pytest.raises(IngestError, match="duplicate show_id"):
        ingest.load_sources(p)


def test_missing_sources_file(tmp_path):
    with pytest.raises(IngestError, match="no sources file"):
        ingest.load_sources(tmp_path / "nope.json")


def test_malformed_sources_file(tmp_path):
    p = tmp_path / "sources.json"
    p.write_text("{not json")
    with pytest.raises(IngestError, match="not valid JSON"):
        ingest.load_sources(p)


# ----------------------------------------------------------------------- tools


def test_check_tools_reports_every_missing_tool(monkeypatch):
    monkeypatch.setattr(ingest.shutil, "which", lambda t: None if t in {"ffmpeg", "ffprobe"} else "/usr/bin/x")
    with pytest.raises(IngestError, match="ffmpeg, ffprobe"):
        ingest.check_tools()


def test_check_tools_passes_when_all_present(monkeypatch):
    monkeypatch.setattr(ingest.shutil, "which", lambda t: "/usr/bin/" + t)
    ingest.check_tools()


def test_main_exits_2_before_downloading_when_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr(ingest.shutil, "which", lambda t: None if t == "ffmpeg" else "/usr/bin/x")
    called = []
    monkeypatch.setattr(ingest, "download", lambda *a, **k: called.append(a))
    assert ingest.main([]) == 2
    assert called == []


# -------------------------------------------------------------------- duration


@pytest.mark.parametrize("secs,msg", [(120.0, "not a full show"), (2400.0, "compilation")])
def test_duration_bounds(secs, msg):
    with pytest.raises(IngestError, match=msg):
        ingest.check_duration(secs, "cav-2004")


def test_duration_ok():
    ingest.check_duration(660.0, "cav-2004")


# ---------------------------------------------------------------------- frames


def test_extract_frames_names_files_by_timestamp(tmp_path, monkeypatch):
    def fake_run(cmd, what):
        out = Path([c for c in cmd if c.endswith("seq%05d.jpg")][0])
        make_frames_seq(out.parent, 5)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def make_frames_seq(d, n):
        from PIL import Image

        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, n + 1):
            Image.new("RGB", (1024, 576), "black").save(d / f"seq{i:05d}.jpg")

    monkeypatch.setattr(ingest, "_run", fake_run)
    times = ingest.extract_frames(tmp_path / "src.mp4", tmp_path / "frames", 8)

    assert times == [0, 8, 16, 24, 32]
    assert sorted(p.name for p in (tmp_path / "frames").glob("*.jpg")) == [
        "t00000.jpg", "t00008.jpg", "t00016.jpg", "t00024.jpg", "t00032.jpg",
    ]


def test_extract_frames_raises_when_ffmpeg_produced_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "_run", lambda cmd, what: subprocess.CompletedProcess(cmd, 0, "", ""))
    with pytest.raises(IngestError, match="no frames extracted"):
        ingest.extract_frames(tmp_path / "src.mp4", tmp_path / "frames", 8)


def test_download_skips_when_present(tmp_path, monkeypatch):
    dest = tmp_path / "source.mp4"
    dest.write_bytes(b"stub")
    monkeypatch.setattr(ingest, "_run", lambda *a: pytest.fail("should not have shelled out"))
    assert ingest.download("https://www.youtube.com/watch?v=abc123", dest) == dest


def test_download_refetches_with_force(tmp_path, monkeypatch):
    dest = tmp_path / "source.mp4"
    dest.write_bytes(b"stub")
    seen = []

    def fake_run(cmd, what):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ingest, "_run", fake_run)
    ingest.download("https://www.youtube.com/watch?v=abc123", dest, force=True)
    assert seen and "--no-playlist" in seen[0]


def test_run_surfaces_stderr_tail():
    with pytest.raises(IngestError, match="boom"):
        ingest._run(
            [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
            "probe",
        )


# ----------------------------------------------------------------------- shape


def shape_fixture(duration=660, n=400):
    import math

    times = [duration * i / n for i in range(n)]
    rms = [0.2 + 0.8 * abs(math.sin(i / 40)) for i in range(n)]
    return AudioShape(times=times, rms=rms, tempo_times=times, tempo=[120 + 20 * math.sin(i / 60) for i in range(n)], onset_density=3.1)


def test_render_shape_is_exact_size_and_readable(tmp_path):
    from PIL import Image

    dest = ingest.render_shape(shape_fixture(), [0, 60, 120, 600], "The Cavaliers 2004", tmp_path / "shape.png")
    with Image.open(dest) as im:
        assert im.size == (ingest.SHAPE_W, ingest.SHAPE_H)
        assert im.convert("RGB").getcolors(maxcolors=1_000_000) is not None


def test_render_shape_survives_empty_tempo(tmp_path):
    s = shape_fixture()
    s.tempo_times, s.tempo = [], []
    dest = ingest.render_shape(s, [0, 60], "x", tmp_path / "shape.png")
    assert dest.exists()


# ---------------------------------------------------------------------- sheets


def test_sheets_cover_every_candidate_exactly_once(tmp_path):
    from PIL import Image

    times = list(range(0, 8 * 45, 8))  # 45 candidates -> 3 sheets (20/20/5)
    frames = make_frames(tmp_path / "frames", times)
    sheets = ingest.build_sheets(frames, tmp_path / "sheets")

    assert len(sheets) == 3
    cell_h = ingest.CELL_H + ingest.LABEL_H
    with Image.open(sheets[0]) as im:
        assert im.size == (ingest.SHEET_COLS * ingest.CELL_W, 5 * cell_h)
    with Image.open(sheets[-1]) as im:  # 5 leftovers -> 2 rows, not a padded 5
        assert im.size == (ingest.SHEET_COLS * ingest.CELL_W, 2 * cell_h)

    covered = sum(
        min(ingest.SHEET_CELLS, len(frames) - i * ingest.SHEET_CELLS) for i in range(len(sheets))
    )
    assert covered == len(frames)


def test_sheets_are_rebuilt_not_appended(tmp_path):
    frames = make_frames(tmp_path / "frames", list(range(0, 8 * 25, 8)))
    ingest.build_sheets(frames, tmp_path / "sheets")
    ingest.build_sheets(frames[:20], tmp_path / "sheets")
    assert sorted(p.name for p in (tmp_path / "sheets").glob("*.jpg")) == ["sheet_01.jpg"]


def test_build_sheets_rejects_empty(tmp_path):
    with pytest.raises(IngestError, match="no frames"):
        ingest.build_sheets([], tmp_path / "sheets")


# ----------------------------------------------------------------- integration


@pytest.fixture
def faked_shell(monkeypatch):
    """Fake the subprocess boundary; everything above it runs for real."""
    from PIL import Image

    state = {"duration": 660.0, "cmds": []}

    def fake_run(cmd, what):
        state["cmds"].append(cmd)
        exe = Path(cmd[0]).name
        if exe == "yt-dlp":
            dest = Path(cmd[cmd.index("-o") + 1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake mp4")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if exe == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, f"{state['duration']}\n", "")
        if exe == "ffmpeg" and any(str(c).endswith("seq%05d.jpg") for c in cmd):
            out = Path([c for c in cmd if str(c).endswith("seq%05d.jpg")][0]).parent
            out.mkdir(parents=True, exist_ok=True)
            # Honour the -vf fps=1/N the caller actually asked for, so frame counts
            # follow the sampling interval the way real ffmpeg would.
            vf = cmd[cmd.index("-vf") + 1]
            interval = int(re.search(r"fps=1/(\d+)", vf).group(1))
            count = int(state["duration"] // interval) + 1
            for i in range(1, count + 1):
                Image.new("RGB", (1024, 576), (i % 256, 30, 60)).save(out / f"seq{i:05d}.jpg")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if exe == "ffmpeg":  # audio
            Path(cmd[-1]).write_bytes(b"RIFF")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(ingest, "_run", fake_run)
    monkeypatch.setattr(ingest, "analyze_audio", lambda wav, n_points=1000: shape_fixture())
    monkeypatch.setattr(ingest.shutil, "which", lambda t: "/usr/bin/" + t)
    return state


def test_ingest_show_end_to_end(tmp_path, faked_shell):
    from PIL import Image

    res = ingest.ingest_show(entry(), out_root=tmp_path)
    d = tmp_path / "cav-2004"

    assert (d / "source.mp4").exists()
    assert (d / "shape.png").exists()
    expected = int(660 // 8) + 1
    assert len(res.frame_times) == expected
    assert res.frame_times[:3] == [0, 8, 16]

    for p in (d / "frames").glob("*.jpg"):
        with Image.open(p) as im:
            assert im.size == (1024, 576)

    with Image.open(d / "shape.png") as im:
        assert im.size == (1024, 384)

    meta = json.loads((d / "ingest.json").read_text())
    assert meta["frame_count"] == expected
    assert meta["interval_s"] == 8
    assert meta["url"] == "https://www.youtube.com/watch?v=6TuKic5Lj5k"
    n_sheets = -(-expected // ingest.SHEET_CELLS)
    assert meta["sheets"] == [f"sheets/sheet_{i:02d}.jpg" for i in range(1, n_sheets + 1)]
    for rel in meta["sheets"]:
        assert (d / rel).exists()


def test_multicam_samples_denser_than_high(tmp_path, faked_shell):
    """Same-length show, tighter interval -> strictly more candidates to shortlist from."""
    hi = ingest.ingest_show(entry(show_id="hi", angle="high"), out_root=tmp_path)
    mc = ingest.ingest_show(entry(show_id="mc", angle="multi-cam"), out_root=tmp_path)

    assert (hi.interval_s, mc.interval_s) == (8, 6)
    assert len(mc.frame_times) > len(hi.frame_times)
    assert len(hi.frame_times) == int(660 // 8) + 1
    assert len(mc.frame_times) == int(660 // 6) + 1
    # Both still span the show; only the density differs.
    assert hi.frame_times[-1] <= 660 and mc.frame_times[-1] <= 660


def test_ingest_passes_no_playlist(tmp_path, faked_shell):
    ingest.ingest_show(entry(), out_root=tmp_path)
    ytdlp = [c for c in faked_shell["cmds"] if Path(c[0]).name == "yt-dlp"][0]
    assert "--no-playlist" in ytdlp
    assert ytdlp[-1] == "https://www.youtube.com/watch?v=6TuKic5Lj5k"


def test_short_video_rejected_after_probe(tmp_path, faked_shell):
    faked_shell["duration"] = 90.0
    with pytest.raises(IngestError, match="not a full show"):
        ingest.ingest_show(entry(), out_root=tmp_path)


def test_main_continues_past_a_failing_show(tmp_path, faked_shell, monkeypatch):
    src = tmp_path / "sources.json"
    src.write_text(json.dumps([
        entry(show_id="good-1"),
        entry(show_id="bad-1", url="https://www.youtube.com/watch?v=zzz999"),
        entry(show_id="good-2"),
    ]))
    real = ingest.ingest_show
    monkeypatch.setattr(
        ingest, "ingest_show",
        lambda e, **kw: (_ for _ in ()).throw(IngestError("nope")) if e["show_id"] == "bad-1" else real(e, **kw),
    )
    assert ingest.main(["--sources", str(src), "--out", str(tmp_path / "raw")]) == 1
    assert (tmp_path / "raw" / "good-1" / "ingest.json").exists()
    assert (tmp_path / "raw" / "good-2" / "ingest.json").exists()


def test_main_unknown_show_id(tmp_path, faked_shell):
    assert ingest.main(["--show", "not-a-show", "--out", str(tmp_path)]) == 2


# ------------------------------------------------------------------- hygiene


def test_corpus_deps_never_leak_into_the_daily_pipeline():
    """requirements-corpus.txt must not be importable from the 3am path."""
    heavy = {"librosa", "matplotlib", "numpy", "PIL", "yt_dlp"}
    offenders = []
    for py in (REPO_ROOT / "scripts").glob("*.py"):
        src = py.read_text()
        for mod in heavy:
            if re.search(rf"^\s*(import|from)\s+{mod}\b", src, re.M):
                offenders.append(f"{py.name} imports {mod}")
    assert not offenders, offenders


def test_corpus_raw_is_gitignored():
    ignore = (REPO_ROOT / ".gitignore").read_text()
    assert "corpus/raw/" in ignore


def test_heavy_imports_are_lazy():
    """Importing ingest must not pull in librosa — CI has no such dependency."""
    import sys

    assert "scripts.corpus.ingest" in sys.modules
    assert "librosa" not in sys.modules


# ------------------------------------------------------- per-show interval override


def test_interval_override_beats_the_angle_default():
    got = ingest.validate_entry(entry(angle="multi-cam", interval_s=3))
    assert got["interval_s"] == 3


def test_interval_override_applies_to_high_angle_too():
    assert ingest.validate_entry(entry(angle="high", interval_s=4))["interval_s"] == 4


def test_absent_override_keeps_the_angle_default():
    assert ingest.validate_entry(entry(angle="multi-cam"))["interval_s"] == 6
    assert ingest.validate_entry(entry(angle="high"))["interval_s"] == 8


@pytest.mark.parametrize("bad", [0, 1, 31, 600, -4])
def test_interval_override_bounds(bad):
    with pytest.raises(IngestError, match="interval_s"):
        ingest.validate_entry(entry(interval_s=bad))


@pytest.mark.parametrize("bad", ["6", 6.0, True, None])
def test_interval_override_must_be_an_int(bad):
    with pytest.raises(IngestError, match="interval_s"):
        ingest.validate_entry(entry(interval_s=bad))


def test_madison_is_sampled_denser_than_the_multicam_default():
    """The show that was ~32% usable at 6s needs a denser sample, not a smaller cap."""
    entries = {e["show_id"]: e for e in ingest.load_sources(REPO_ROOT / "corpus" / "sources.json")}
    mad = entries["mad-1995"]
    assert mad["angle"] == "multi-cam"
    assert mad["interval_s"] == 3
    assert mad["interval_s"] < ingest.SAMPLE_INTERVAL_S["multi-cam"]


def test_override_reaches_extraction(tmp_path, faked_shell):
    res = ingest.ingest_show(entry(show_id="dense", angle="multi-cam", interval_s=3), out_root=tmp_path)
    assert res.interval_s == 3
    assert len(res.frame_times) == int(660 // 3) + 1
    assert res.frame_times[:3] == [0, 3, 6]
    meta = json.loads((tmp_path / "dense" / "ingest.json").read_text())
    assert meta["interval_s"] == 3
