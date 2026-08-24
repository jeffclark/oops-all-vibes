"""Daily corpus selection: what Georgia looks at this morning.

The only part of the corpus that runs inside the 3am cron, and the only part
with a hard fail-open requirement. Everything here reads JSON and builds dicts —
no Pillow, no numpy, nothing from requirements-corpus.txt. The daily Actions run
installs `requirements.txt` and nothing else, so an import from this module into
the heavy half of the package would take the site down on the next deploy, not
at test time.

**Fail open, always.** Site and diary are hard requirements; the corpus is
additive. A missing manifest, malformed JSON, an empty frame list or anything
unexpected returns an empty selection and the day ships text-only with a warning
on stderr.

`selection_for_date` is the entry point the daily run uses and it cannot raise:
its bare `except Exception` swallows everything, including the image-cap
assertion, and degrades to an empty selection. `select_for_date` — the pure
function underneath — *can* raise that assertion, deliberately, so tests can see
an over-cap manifest for what it is. Call the wrapper from anything on the daily
path; run_georgia does not guard this call itself.

**The shape of a day**: every anchor in the manifest, plus 6 rotating frames, plus
up to 4 show-shape plots, capped at 20 image blocks.

- **Anchors, every day, forever.** Repetition is the mechanism — a preference
  cannot form from a single viewing. The count comes from the manifest and is not
  asserted to be 10: story_015 deliberately permits a smaller corpus to publish
  fewer, and an `assert len(anchors) == 10` here would break that case.
- **Rotation is dumb and deterministic**, seeded from the date string alone, so a
  replay of any past day reproduces exactly what she saw. 6 slots against a
  ~180-frame pool brings a given frame back about monthly: often enough to notice
  you have seen it before, rarely enough to become wallpaper.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_PATH = REPO_ROOT / "corpus" / "manifest.json"

ROTATING_PER_DAY = 6
MAX_SHAPES_PER_DAY = 4

# Ours, not the API's. 20 images is a cost and attention budget: a set she can
# hold in mind beats a set she skims. (The API's stricter above-20 rule only
# applies over 2000px a side; ours are 1024x576 and 1024x384, so it would not
# bite either way.)
MAX_IMAGE_BLOCKS = 20


def _warn(msg: str) -> None:
    print(f"corpus_select: {msg}", file=sys.stderr)


@dataclass(frozen=True)
class CorpusSelection:
    """What was chosen, in the order it is shown.

    `frame_ids` is threaded to four places: the prompt (prior verdicts and the
    sentinel choice), the prompt archive, the `<taste>` parser, and — as `blocks`
    — the request itself.
    """

    frame_ids: tuple[str, ...] = ()
    anchor_ids: tuple[str, ...] = ()
    rotating_ids: tuple[str, ...] = ()
    shape_show_ids: tuple[str, ...] = ()
    blocks: tuple[dict[str, Any], ...] = ()
    manifest_version: int | None = None

    @property
    def shown(self) -> bool:
        return bool(self.frame_ids)

    @property
    def image_count(self) -> int:
        return sum(1 for b in self.blocks if b.get("type") == "image")


EMPTY = CorpusSelection()


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any] | None:
    """The manifest, or None with a warning. Never raises."""
    try:
        if not path.exists():
            _warn(f"no manifest at {path}; running text-only")
            return None
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _warn(f"manifest at {path} is unreadable ({exc}); running text-only")
        return None
    if not isinstance(payload, dict) or not payload.get("frames"):
        _warn("manifest has no frames; running text-only")
        return None
    return payload


def _rotation_key(date_str: str, frame_id: str) -> str:
    """Stable per (date, frame) ordering key.

    A hash rather than `random.Random(seed).sample`: no module-global state, no
    dependence on a particular CPython version's RNG, and a replay of any past
    date reproduces byte-for-byte on any machine.
    """
    return hashlib.sha256(f"{date_str}:{frame_id}".encode()).hexdigest()


def _frame_block(frame: dict[str, Any]) -> list[dict[str, Any]]:
    """A short label naming the frame, then the frame.

    The label is what lets her refer to a frame at all, and what the preference
    log keys on. Referencing by `file_id` keeps the payload tiny however large the
    corpus grows.
    """
    return [
        {"type": "text", "text": f"[{frame['frame_id']}]"},
        {"type": "image", "source": {"type": "file", "file_id": frame["file_id"]}},
    ]


def _shape_block(show_id: str, file_id: str) -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": f"[shape:{show_id}] loudness and tempo over the show"},
        {"type": "image", "source": {"type": "file", "file_id": file_id}},
    ]


def select_for_date(
    run_date: date,
    manifest: dict[str, Any] | None,
    rotating_per_day: int = ROTATING_PER_DAY,
    max_shapes: int = MAX_SHAPES_PER_DAY,
) -> CorpusSelection:
    """Today's frames and the image blocks that carry them."""
    if not manifest:
        return EMPTY

    frames = [f for f in manifest.get("frames", []) if f.get("file_id") and f.get("frame_id")]
    anchors = [f for f in frames if f.get("role") == "anchor"]
    pool = [f for f in frames if f.get("role") != "anchor"]

    date_str = run_date.isoformat()
    chosen = sorted(pool, key=lambda f: _rotation_key(date_str, f["frame_id"]))[:rotating_per_day]
    # Presented in a stable order rather than hash order, so the sequence she sees
    # reads as a shelf rather than as a shuffle.
    rotating = sorted(chosen, key=lambda f: (f["show_id"], f.get("curation_rank", 0)))

    shapes_by_show = {s["show_id"]: s["file_id"] for s in manifest.get("shapes", []) if s.get("file_id")}
    # Which shows get a shape plot is drawn from the rotation order, not the display
    # order. Taking the first four of a show_id-sorted list would hand the audio
    # shape to alphabetically-early shows almost every day and to `star-1993` almost
    # never, which is a bias the rotation was specifically built not to have.
    shape_shows: list[str] = []
    for f in chosen:
        show = f["show_id"]
        if show in shapes_by_show and show not in shape_shows:
            shape_shows.append(show)
    shape_shows = shape_shows[:max_shapes]

    # Over the cap, shed in order of information per token: the shape plots first,
    # then the rotating frames she ranked lowest. Anchors are never dropped —
    # losing one breaks the repetition the whole corpus is built on.
    over = len(anchors) + len(rotating) + len(shape_shows) - MAX_IMAGE_BLOCKS
    if over > 0:
        drop_shapes = min(over, len(shape_shows))
        shape_shows = shape_shows[:len(shape_shows) - drop_shapes]
        over -= drop_shapes
    if over > 0:
        rotating.sort(key=lambda f: f.get("curation_rank", 0))
        keep = max(0, len(rotating) - over)
        # lowest-ranked go first; keep the strongest, then restore display order
        rotating = sorted(rotating[:keep], key=lambda f: (f["show_id"], f.get("curation_rank", 0)))

    ordered = anchors + rotating
    blocks: list[dict[str, Any]] = []
    for f in ordered:
        blocks.extend(_frame_block(f))
    for show in shape_shows:
        blocks.extend(_shape_block(show, shapes_by_show[show]))

    selection = CorpusSelection(
        frame_ids=tuple(f["frame_id"] for f in ordered),
        anchor_ids=tuple(f["frame_id"] for f in anchors),
        rotating_ids=tuple(f["frame_id"] for f in rotating),
        shape_show_ids=tuple(shape_shows),
        blocks=tuple(blocks),
        manifest_version=manifest.get("version"),
    )
    # Ours to enforce: a future manifest change must not silently push past it.
    assert selection.image_count <= MAX_IMAGE_BLOCKS, (
        f"corpus selection built {selection.image_count} image blocks, "
        f"over the {MAX_IMAGE_BLOCKS} cap"
    )
    return selection


def selection_for_date(run_date: date, manifest_path: Path = MANIFEST_PATH) -> CorpusSelection:
    """load + select, with every failure degrading to an empty selection.

    This is the entry point the daily run uses. It cannot raise: a corpus problem
    must never be able to cost a day of site.
    """
    try:
        return select_for_date(run_date, load_manifest(manifest_path))
    except Exception as exc:  # noqa: BLE001 — the corpus may never take the site down
        _warn(f"could not build a corpus selection ({exc}); running text-only")
        return EMPTY


def build_content(selection: CorpusSelection, prompt: str) -> list[dict[str, Any]] | str:
    """Images first, then the text prompt. A bare string when there is no corpus.

    Image-before-text measurably helps, and the string path keeps every existing
    caller and the text-only day on exactly the shape they had before.
    """
    if not selection.blocks:
        return prompt
    return list(selection.blocks) + [{"type": "text", "text": prompt}]
