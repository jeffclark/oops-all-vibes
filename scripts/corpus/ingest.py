"""Corpus ingest: a show video becomes candidate frames, an audio shape, and contact sheets.

Offline. Runs on Jeff's machine, once per show, never in CI — it shells out to
yt-dlp/ffmpeg and pulls in librosa, none of which belong in the 3am pipeline.

The three outputs, and why each exists:

- **Frames** are what Georgia can actually perceive. She has no video and no audio
  input, so stills are the entire menu. Drill survives the transition better than
  most footage because a form on a 100-yard grid is already designed to be read as
  a static composition from above.
- **shape.png** is how she gets at music she cannot hear: the loudness and tempo of
  the real performance over time, with a tick at every candidate frame so a still
  can be located in the show's arc. Derived from the audio itself, never from
  somebody's description of it.
- **Contact sheets** exist so Jeff can eyeball a show before curating it. `angle` in
  sources.json is self-declared and nothing validates it automatically; the sheets
  are the only check on a show that claims a high angle and was really shot from
  the stands.

Nothing here forms an opinion about what is on the frames. That is story_014's job,
and it is Georgia's.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCES_PATH = REPO_ROOT / "corpus" / "sources.json"
RAW_ROOT = REPO_ROOT / "corpus" / "raw"

# Frame geometry. 1024x576 costs ceil(1024/28) * ceil(576/28) = 777 visual tokens.
# 640x360 would cost 299, but at that size a 150-person field form is a few pixels
# per performer and drill stops being legible. The daily difference is under a cent.
# Do not shrink these to save tokens.
FRAME_W, FRAME_H = 1024, 576
SHAPE_W, SHAPE_H = 1024, 384

# Sampling interval by camera angle. multi-cam is a broadcast cut that alternates
# between a high angle and close-ups, so a share of its frames land on a shot that
# is useless for drill; the tighter interval keeps enough high-angle candidates to
# shortlist 25 from. No automatic shot detection — curation is already the filter.
SAMPLE_INTERVAL_S = {"high": 8, "press-box": 8, "multi-cam": 6}
REJECTED_ANGLES = {"field-level"}

# A per-show `interval_s` in sources.json overrides the angle default. The angle
# alone turned out to be too coarse: what matters is not whether a broadcast cuts
# around but what fraction of the cut sits on the field, and that varies per show.
# Madison 1995 runs about a third high-angle, so at 6s only ~32 of 100 candidates
# were usable — and round 1 asks Georgia to shortlist 25. Picking 25 of 32 is not
# forced choice, and forced choice is the only thing that makes this taste rather
# than appreciation. Sample denser until the usable pool is comfortably larger
# than the shortlist.
#
# Rule of thumb from a sheet: usable cells per sheet / 20 = keep rate. Aim for a
# usable pool of at least ~2.5x the shortlist target, so:
#     interval = default_interval * keep_rate * 2.5   (rounded down, floor 2)
MIN_INTERVAL_S = 2
MAX_INTERVAL_S = 30

# A show shorter than this isn't a full show and its arc won't mean anything;
# longer than this and something other than a competitive run was linked.
MIN_DURATION_S = 240.0
MAX_DURATION_S = 1200.0

# 4 columns x 5 rows is deliberate over 5x4: a squarer sheet wastes less against the
# API's long-edge cap, so cells survive its downscale at roughly 577x324 instead of
# 515x290. Enough to judge form, staging and colour; not enough for detail, which is
# correct for a shortlisting pass.
SHEET_COLS, SHEET_ROWS = 4, 5
SHEET_CELLS = SHEET_COLS * SHEET_ROWS
CELL_W, CELL_H = 640, 360
LABEL_H = 26

REQUIRED_TOOLS = ("yt-dlp", "ffmpeg", "ffprobe")
REQUIRED_KEYS = ("show_id", "url", "corps", "year", "angle", "axis_tags")
SHOW_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
VIDEO_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/embed/)([\w-]{6,})")


class IngestError(Exception):
    """Anything that should stop an ingest with a message a human can act on."""


@dataclass
class ShowIngest:
    show_id: str
    corps: str
    year: int
    angle: str
    url: str
    duration_s: float
    interval_s: int
    frame_times: list[int]
    sheets: list[str]
    axis_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "show_id": self.show_id,
            "corps": self.corps,
            "year": self.year,
            "angle": self.angle,
            "url": self.url,
            "duration_s": round(self.duration_s, 2),
            "interval_s": self.interval_s,
            "frame_count": len(self.frame_times),
            "frame_times": self.frame_times,
            "sheets": self.sheets,
            "axis_tags": self.axis_tags,
        }


def _log(msg: str) -> None:
    print(f"ingest: {msg}", file=sys.stderr)


# ---------------------------------------------------------------- validation


def canonical_url(url: str) -> str:
    """Reduce a YouTube URL to the bare single-video form.

    Most supplied links carry `&list=` or `start_radio=1`. yt-dlp is also passed
    --no-playlist, but stripping the parameters here means the committed manifest
    records one unambiguous video rather than a playlist position that can shift.
    """
    m = VIDEO_ID_RE.search(url)
    if not m:
        raise IngestError(f"not a recognisable YouTube URL: {url!r}")
    return f"https://www.youtube.com/watch?v={m.group(1)}"


def sample_interval(angle: str) -> int:
    try:
        return SAMPLE_INTERVAL_S[angle]
    except KeyError:
        raise IngestError(
            f"unknown angle {angle!r}; expected one of {sorted(SAMPLE_INTERVAL_S)}"
        ) from None


def validate_entry(entry: Any) -> dict[str, Any]:
    """Return a normalised entry, or raise IngestError naming what is wrong."""
    if not isinstance(entry, dict):
        raise IngestError(f"source entry must be an object, got {type(entry).__name__}")

    missing = [k for k in REQUIRED_KEYS if k not in entry]
    if missing:
        raise IngestError(f"source entry missing {', '.join(missing)}: {entry!r}")

    show_id = entry["show_id"]
    if not isinstance(show_id, str) or not SHOW_ID_RE.match(show_id):
        raise IngestError(f"show_id must be lowercase slug, got {show_id!r}")

    angle = entry["angle"]
    if angle in REJECTED_ANGLES:
        raise IngestError(
            f"{show_id}: angle is {angle!r}. Footage shot from the stands shows a wall "
            "of backs and contributes nothing about drill — it would fill ten corpus "
            "slots with frames Georgia cannot form a real preference about. Replace "
            "the source with high-angle footage, or drop the show."
        )
    interval = sample_interval(angle)
    if "interval_s" in entry:
        override = entry["interval_s"]
        if not isinstance(override, int) or isinstance(override, bool):
            raise IngestError(f"{show_id}: interval_s must be an int, got {override!r}")
        if not MIN_INTERVAL_S <= override <= MAX_INTERVAL_S:
            raise IngestError(
                f"{show_id}: interval_s {override} outside "
                f"{MIN_INTERVAL_S}-{MAX_INTERVAL_S}s"
            )
        interval = override

    year = entry["year"]
    if not isinstance(year, int) or isinstance(year, bool) or not 1970 <= year <= 2100:
        raise IngestError(f"{show_id}: year must be a plausible int, got {year!r}")

    tags = entry["axis_tags"]
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise IngestError(f"{show_id}: axis_tags must be a list of strings")

    return {
        **entry,
        "url": canonical_url(entry["url"]),
        "interval_s": interval,
        "axis_tags": list(tags),
    }


def load_sources(path: Path = SOURCES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        raise IngestError(f"no sources file at {path}")
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise IngestError(f"{path.name} is not valid JSON: {exc}") from None
    if not isinstance(raw, list) or not raw:
        raise IngestError(f"{path.name} must be a non-empty list of shows")

    entries = [validate_entry(e) for e in raw]
    seen: set[str] = set()
    for e in entries:
        if e["show_id"] in seen:
            raise IngestError(f"duplicate show_id {e['show_id']!r}")
        seen.add(e["show_id"])
    return entries


def check_tools(tools: Sequence[str] = REQUIRED_TOOLS) -> None:
    """Fail before downloading anything if the toolchain is incomplete."""
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        raise IngestError(
            f"missing required tool(s) on PATH: {', '.join(missing)}. "
            "Install ffmpeg (which provides ffprobe) and `pip install -r "
            "requirements-corpus.txt`, then re-run."
        )


# ------------------------------------------------------------------ shelling


def _run(cmd: Sequence[str], what: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise IngestError(f"{what} failed (exit {proc.returncode}):\n  " + "\n  ".join(tail))
    return proc


def download(url: str, dest: Path, force: bool = False) -> Path:
    if dest.exists() and not force:
        _log(f"{dest.name} already present, skipping download (use --force to refetch)")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "yt-dlp",
            "--no-playlist",  # supplied URLs carry &list= / start_radio=1
            "--no-progress",
            "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "--merge-output-format", "mp4",
            "-o", str(dest),
            url,
        ],
        f"yt-dlp {url}",
    )
    if not dest.exists():
        raise IngestError(f"yt-dlp reported success but {dest} is missing")
    return dest


def probe_duration(src: Path) -> float:
    proc = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
        f"ffprobe {src.name}",
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise IngestError(f"could not read a duration from {src.name}") from None


def check_duration(seconds: float, show_id: str) -> None:
    if seconds < MIN_DURATION_S:
        raise IngestError(
            f"{show_id}: {seconds:.0f}s is under the {MIN_DURATION_S:.0f}s floor — "
            "that is a clip or an excerpt, not a full show, and its arc will not mean "
            "anything."
        )
    if seconds > MAX_DURATION_S:
        raise IngestError(
            f"{show_id}: {seconds:.0f}s is over the {MAX_DURATION_S:.0f}s ceiling — "
            "this is probably a full broadcast or a compilation rather than one run."
        )


def extract_frames(src: Path, out_dir: Path, interval_s: int) -> list[int]:
    """Extract one frame every `interval_s`, named by its source timestamp.

    A single ffmpeg invocation, not one per frame. ffmpeg numbers its output
    sequentially, so the files are renamed afterwards to carry the timestamp —
    that timestamp is the provenance record and has to survive into the manifest.
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    _run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(src),
         "-vf", f"fps=1/{interval_s},scale={FRAME_W}:{FRAME_H}",
         "-q:v", "4", str(out_dir / "seq%05d.jpg")],
        f"ffmpeg frames {src.name}",
    )
    times: list[int] = []
    for i, seq in enumerate(sorted(out_dir.glob("seq*.jpg"))):
        t = i * interval_s
        seq.rename(out_dir / f"t{t:05d}.jpg")
        times.append(t)
    if not times:
        raise IngestError(f"no frames extracted from {src.name}")
    return times


def extract_audio(src: Path, dest: Path) -> Path:
    _run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(src), "-vn",
         "-ac", "1", "-ar", "22050", "-f", "wav", str(dest)],
        f"ffmpeg audio {src.name}",
    )
    return dest


# -------------------------------------------------------------------- shape


@dataclass
class AudioShape:
    times: list[float]
    rms: list[float]
    tempo_times: list[float]
    tempo: list[float]
    onset_density: float


def analyze_audio(wav: Path, n_points: int = 1000) -> AudioShape:
    """Loudness envelope, tempo curve and onset density for the real performance.

    librosa is imported here rather than at module scope so that everything else
    in this module — validation, sheets, plotting — stays importable and testable
    without the heavy dependency.
    """
    import librosa  # noqa: PLC0415 — deliberate local import, see docstring
    import numpy as np

    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    if y.size == 0:
        raise IngestError(f"{wav.name} decoded to zero samples")

    hop = max(1, y.size // n_points)
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    times = librosa.times_like(rms, sr=sr, hop_length=hop)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo = librosa.feature.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
    tempo_times = librosa.times_like(tempo, sr=sr)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)

    duration = float(y.size / sr)
    return AudioShape(
        times=[float(t) for t in times],
        rms=[float(v) for v in rms],
        tempo_times=[float(t) for t in tempo_times],
        tempo=[float(v) for v in np.atleast_1d(tempo)],
        onset_density=float(len(onsets) / duration) if duration else 0.0,
    )


def render_shape(shape: AudioShape, frame_times: Sequence[int], title: str, dest: Path) -> Path:
    """Draw the show's arc: loudness, tempo, and a tick per candidate frame.

    This is the only channel through which anything about the sound reaches
    Georgia. Keep it a picture of the performance, not an annotation of it — no
    labelled "climax", no interpretation. She reads the shape herself.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dpi = 100
    fig, ax = plt.subplots(figsize=(SHAPE_W / dpi, SHAPE_H / dpi), dpi=dpi)
    fig.patch.set_facecolor("#111111")
    ax.set_facecolor("#111111")

    ax.fill_between(shape.times, shape.rms, color="#e8e8e8", alpha=0.85, linewidth=0)
    ax.set_xlim(0, max(shape.times) if shape.times else 1)
    ax.set_ylim(0, (max(shape.rms) * 1.15) if shape.rms else 1)
    ax.set_yticks([])

    if shape.tempo_times and shape.tempo:
        ax2 = ax.twinx()
        n = min(len(shape.tempo_times), len(shape.tempo))
        ax2.plot(shape.tempo_times[:n], shape.tempo[:n], color="#ff7a45", linewidth=1.0, alpha=0.75)
        ax2.set_ylabel("tempo", color="#ff7a45", fontsize=8)
        ax2.tick_params(axis="y", colors="#ff7a45", labelsize=7)
        for side in ("top", "left", "right", "bottom"):
            ax2.spines[side].set_visible(False)

    # One tick per candidate frame, so a still can be placed in the arc.
    for t in frame_times:
        ax.axvline(t, ymin=0.0, ymax=0.055, color="#4da3ff", linewidth=0.7, alpha=0.9)

    ax.set_xlabel("seconds", color="#888888", fontsize=8)
    ax.tick_params(axis="x", colors="#888888", labelsize=7)
    for side in ("top", "left", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#444444")
    ax.set_title(title, color="#dddddd", fontsize=10, loc="left")

    fig.tight_layout(pad=0.6)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, facecolor=fig.get_facecolor(), dpi=dpi)
    plt.close(fig)

    _force_size(dest, SHAPE_W, SHAPE_H)
    return dest


def _force_size(path: Path, w: int, h: int) -> None:
    """tight_layout can land a pixel or two off; the AC is an exact size."""
    from PIL import Image

    with Image.open(path) as im:
        if im.size != (w, h):
            im.convert("RGB").resize((w, h), Image.LANCZOS).save(path)


# ------------------------------------------------------------------- sheets


def _label_font(size: int):
    from PIL import ImageFont

    for name in ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no size argument
        return ImageFont.load_default()


def build_sheets(frame_paths: Sequence[Path], out_dir: Path, title: str = "") -> list[Path]:
    """Compose candidates into labelled contact sheets, 20 cells each.

    Every candidate appears in exactly one cell — a sheet that quietly dropped
    frames would hide part of the show from the person checking the camera angle,
    which is the entire reason the sheets exist.
    """
    from PIL import Image, ImageDraw

    if not frame_paths:
        raise IngestError("no frames to build sheets from")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    font = _label_font(20)
    cell_h = CELL_H + LABEL_H
    sheets: list[Path] = []

    for n, start in enumerate(range(0, len(frame_paths), SHEET_CELLS), start=1):
        chunk = list(frame_paths[start:start + SHEET_CELLS])
        rows = (len(chunk) + SHEET_COLS - 1) // SHEET_COLS
        sheet = Image.new("RGB", (SHEET_COLS * CELL_W, rows * cell_h), "#111111")
        draw = ImageDraw.Draw(sheet)

        for i, fp in enumerate(chunk):
            col, row = i % SHEET_COLS, i // SHEET_COLS
            x, y = col * CELL_W, row * cell_h
            with Image.open(fp) as im:
                sheet.paste(im.convert("RGB").resize((CELL_W, CELL_H), Image.LANCZOS), (x, y))
            secs = int(fp.stem.lstrip("t"))
            draw.text(
                (x + 8, y + CELL_H + 4),
                f"t={secs}s  ({secs // 60}:{secs % 60:02d})",
                fill="#cccccc",
                font=font,
            )

        dest = out_dir / f"sheet_{n:02d}.jpg"
        sheet.save(dest, quality=88)
        sheets.append(dest)

    if title:
        _log(f"{title}: {len(sheets)} sheet(s), {len(frame_paths)} candidates")
    return sheets


# ---------------------------------------------------------------- orchestration


def ingest_show(entry: dict[str, Any], out_root: Path = RAW_ROOT, force: bool = False) -> ShowIngest:
    entry = validate_entry(entry)
    show_id = entry["show_id"]
    interval = entry["interval_s"]
    show_dir = out_root / show_id
    show_dir.mkdir(parents=True, exist_ok=True)

    src = download(entry["url"], show_dir / "source.mp4", force=force)
    duration = probe_duration(src)
    check_duration(duration, show_id)

    _log(f"{show_id}: {duration:.0f}s, sampling every {interval}s ({entry['angle']})")
    frame_times = extract_frames(src, show_dir / "frames", interval)
    frame_paths = [show_dir / "frames" / f"t{t:05d}.jpg" for t in frame_times]

    title = f"{entry['corps']} {entry['year']}"
    with tempfile.TemporaryDirectory() as tmp:
        wav = extract_audio(src, Path(tmp) / "audio.wav")
        shape = analyze_audio(wav)
    render_shape(shape, frame_times, title, show_dir / "shape.png")

    sheets = build_sheets(frame_paths, show_dir / "sheets", title=show_id)

    result = ShowIngest(
        show_id=show_id,
        corps=entry["corps"],
        year=entry["year"],
        angle=entry["angle"],
        url=entry["url"],
        duration_s=duration,
        interval_s=interval,
        frame_times=frame_times,
        sheets=[str(p.relative_to(show_dir)) for p in sheets],
        axis_tags=entry["axis_tags"],
    )
    (show_dir / "ingest.json").write_text(json.dumps(result.to_dict(), indent=2) + "\n")
    _log(f"{show_id}: {len(frame_times)} candidates, {len(sheets)} sheets — open them before curating")
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ingest show video into corpus candidates (offline; not for CI).",
    )
    p.add_argument("--show", help="ingest only this show_id (default: every entry)")
    p.add_argument("--force", action="store_true", help="re-download even if source.mp4 exists")
    p.add_argument("--sources", type=Path, default=SOURCES_PATH)
    p.add_argument("--out", type=Path, default=RAW_ROOT)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        check_tools()
        entries = load_sources(args.sources)
    except IngestError as exc:
        _log(str(exc))
        return 2

    if args.show:
        entries = [e for e in entries if e["show_id"] == args.show]
        if not entries:
            _log(f"no show with show_id {args.show!r} in {args.sources.name}")
            return 2

    failed: list[str] = []
    for entry in entries:
        try:
            ingest_show(entry, out_root=args.out, force=args.force)
        except IngestError as exc:
            _log(f"{entry['show_id']}: {exc}")
            failed.append(entry["show_id"])

    if failed:
        _log(f"{len(failed)} show(s) failed: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
