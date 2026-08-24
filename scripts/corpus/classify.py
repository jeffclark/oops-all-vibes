"""Vision fallback classifier: ask a cheap model which frames show drill.

Offline tooling, same rule as everything else under `scripts/corpus/` — nothing
in the 3am pipeline may import this.

**Why this exists.** `framing.field_score` measures the fraction of a frame that
reads as lit turf. That works when turf is green and bright. It fails outright
on footage where the playing surface does not read as green at all, and when it
fails it fails silently: every frame scores near zero and the whole show lands in
`other_*`.

The story predicted a heavily tarped modern field as the cause. The real cause,
found on the 2000s shows, is duller: aged broadcast tape of a dry, warm-lit,
worn field. Phantom Regiment 2003 is uninterrupted press-box drill in every
single candidate and scores 0.01-0.04, because the turf is desaturated khaki
rather than saturated green. Star of Indiana 1993 is a night show whose real
drill sits at 0.10-0.19 under a thick band of dark crowd. No threshold move
rescues either without dragging genuine close-ups back in with them, which is
exactly the "chasing the constant" the scorer's own docstring warns against.

So for those shows only, the question is asked directly instead of inferred from
colour. It is a *framing* question, deliberately narrow: is this an elevated shot
of people in formation across the field? Nothing here has an opinion about
whether the formation is any good. That judgement is Georgia's, in story_014.

Frames are never deleted under either mechanism — the verdict only decides which
contact sheet a frame lands on, and `ingest.json` records which mechanism split
the show so the corpus does not quietly become two differently-judged halves.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from scripts.corpus.ingest import RAW_ROOT, IngestError, _log

# Haiku is the right tier for this: it is a category call with a yes/no answer,
# not authorship. ~777 visual tokens a frame at Haiku rates is about $0.16 for a
# 100-candidate show, and only the shows that need it ever pay.
MODEL = "claude-haiku-4-5"

# 20 frames a request keeps each call around 15.5k input tokens, well clear of
# any limit, and keeps one bad batch from costing the whole show.
BATCH_SIZE = 20

# 20 verdicts of a boolean and one clause each. 4000 is generous.
MAX_TOKENS = 4000

MECHANISM_CLASSIFIER = "classifier"
MECHANISM_HEURISTIC = "heuristic"

CLASSIFIED_FILENAME = "classified.json"

SYSTEM = (
    "You are sorting still frames pulled from a marching band / drum corps show "
    "video into two piles, so that a later pass only has to look at the useful "
    "pile. This is a framing judgement about the camera, not a judgement about "
    "the performance. Never comment on whether a formation is good, interesting "
    "or well executed."
)

QUESTION = (
    "For each labelled frame, answer one question about the camera: is this an "
    "elevated shot looking down onto the playing field, wide enough to show where "
    "performers are standing in relation to each other?\n"
    "\n"
    "field = true  — an elevated wide or mid shot from the press box, upper deck or "
    "high sideline. Still true when part of the frame is crowd, stands, roof or sky; "
    "when the surface is tarped, painted, dark, worn or not green; and when the "
    "performers are scattered, sparse, mid-transition or not in any tidy shape at "
    "all. A half-formed or dispersed set seen from above is a `true`.\n"
    "field = false — a close-up or field-level shot: faces, hands, one or a few "
    "performers filling the frame, the front ensemble/pit, the conductor, the crowd, "
    "the scoreboard, a title card, a black or blurred frame, or any angle low enough "
    "that you are looking at the side of people rather than down at the ground they "
    "stand on.\n"
    "\n"
    "Decide on the camera position and how much of the field you can see. Whether the "
    "arrangement is tight, legible, interesting or well executed is explicitly not "
    "your call — a later pass makes that judgement and needs the loose sets in front "
    "of it.\n"
    "\n"
    "Return one verdict for every frame you were shown, keyed by the `t` value in "
    "its label, in the order shown. `reason` is one short clause, no more."
)

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "t": {"type": "integer"},
                    "field": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["t", "field", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


@dataclass
class Verdict:
    t: int
    field: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"t": self.t, "field": self.field, "reason": self.reason}


def load_ingest(show_dir: Path) -> dict[str, Any]:
    path = show_dir / "ingest.json"
    if not path.exists():
        raise IngestError(
            f"no ingest.json in {show_dir} — run `python -m scripts.corpus.ingest "
            f"--show {show_dir.name}` first"
        )
    return json.loads(path.read_text())


def frame_path(show_dir: Path, t: int) -> Path:
    return show_dir / "frames" / f"t{t:05d}.jpg"


def _image_block(path: Path) -> dict[str, Any]:
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
    }


def build_batch_content(show_dir: Path, times: Sequence[int]) -> list[dict[str, Any]]:
    """Label-then-image, repeated, then the question.

    The label goes before its image so the model has the key in hand as it looks,
    rather than having to count backwards from the end to work out which frame is
    which.
    """
    content: list[dict[str, Any]] = []
    for t in times:
        content.append({"type": "text", "text": f"Frame t={t}"})
        content.append(_image_block(frame_path(show_dir, t)))
    content.append({"type": "text", "text": QUESTION})
    return content


def classify_batch(
    client: Any, show_dir: Path, times: Sequence[int]
) -> list[Verdict]:
    """One request for up to BATCH_SIZE frames. Returns verdicts for the ones named."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": build_batch_content(show_dir, times)}],
        output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestError(f"classifier returned unparseable JSON: {exc}") from None

    wanted = set(times)
    seen: set[int] = set()
    out: list[Verdict] = []
    for row in payload.get("verdicts", []):
        t = row.get("t")
        if t not in wanted or t in seen:
            # A hallucinated or duplicated timestamp is dropped rather than
            # trusted; the caller notices as a coverage gap and fills it.
            continue
        seen.add(t)
        out.append(Verdict(t=int(t), field=bool(row.get("field")), reason=str(row.get("reason", "")).strip()))
    return out


def _batched(items: Sequence[int], size: int) -> Iterable[Sequence[int]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def classify_show(
    show_id: str,
    out_root: Path = RAW_ROOT,
    client: Any | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Classify every candidate of one show and write classified.json."""
    show_dir = out_root / show_id
    dest = show_dir / CLASSIFIED_FILENAME
    if dest.exists() and not force:
        _log(f"{show_id}: already classified ({dest.name}); use --force to redo")
        try:
            return json.loads(dest.read_text())
        except json.JSONDecodeError as exc:
            raise IngestError(
                f"{show_id}: {dest} is not valid JSON ({exc}). Delete it or pass "
                "--force to re-classify."
            ) from None

    data = load_ingest(show_dir)
    times: list[int] = list(data.get("frame_times") or [])
    if not times:
        raise IngestError(f"{show_id}: ingest.json lists no frame_times")

    missing = [t for t in times if not frame_path(show_dir, t).exists()]
    if missing:
        raise IngestError(
            f"{show_id}: {len(missing)} frame(s) named in ingest.json are not on "
            f"disk (first: t{missing[0]:05d}.jpg) — re-run ingest for this show"
        )

    if client is None:
        from anthropic import Anthropic

        client = Anthropic()

    verdicts: list[Verdict] = []
    for n, batch in enumerate(_batched(times, BATCH_SIZE), start=1):
        _log(f"{show_id}: classifying frames {batch[0]}-{batch[-1]} (batch {n})")
        verdicts.extend(classify_batch(client, show_dir, batch))

    covered = {v.t for v in verdicts}
    gaps = [t for t in times if t not in covered]
    if gaps:
        raise IngestError(
            f"{show_id}: classifier returned no verdict for {len(gaps)} frame(s) "
            f"(first t={gaps[0]}). Not writing a partial classified.json — re-run."
        )

    payload = {
        "show_id": show_id,
        "model": MODEL,
        "classified_at": date.today().isoformat(),
        "verdicts": [v.to_dict() for v in sorted(verdicts, key=lambda v: v.t)],
    }
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(dest)
    kept = sum(1 for v in verdicts if v.field)
    _log(f"{show_id}: classifier kept {kept}/{len(verdicts)} as field frames")
    return payload


def load_classified(show_dir: Path) -> dict[int, bool] | None:
    """Timestamp -> field verdict, or None when this show has no classifier run.

    Returns None rather than raising on a malformed file: a broken classified.json
    should drop the show back to the heuristic, not stop the ingest.
    """
    path = show_dir / CLASSIFIED_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        rows = payload["verdicts"]
        return {int(r["t"]): bool(r["field"]) for r in rows}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        _log(f"{show_dir.name}: {CLASSIFIED_FILENAME} is unusable ({exc}); using the heuristic")
        return None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Classify one show's candidate frames as field/not-field with a vision "
            "model. Only for shows the green-fraction scorer fails on (offline; not for CI)."
        ),
    )
    p.add_argument("--show", required=True, help="show_id to classify (no bulk mode)")
    p.add_argument("--force", action="store_true", help="re-classify a show already done")
    p.add_argument("--out", type=Path, default=RAW_ROOT)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        classify_show(args.show, out_root=args.out, force=args.force)
    except IngestError as exc:
        _log(str(exc))
        return 2
    _log(f"{args.show}: now re-run `python -m scripts.corpus.ingest --show {args.show}` "
         "to rebuild the sheets from the classifier's split")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
