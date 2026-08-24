"""Anchor drift: does she have taste, or a very convincing average?

She asked the question; the corpus makes it answerable. Because the set is finite
and the anchors are fixed, she writes about the same ten frames across months —
so what she said at day 5 can be put next to what she says at day 120 and the
relationship classified.

**This story reads the record and reports. It changes nothing about what she
sees, adds nothing to the prompt, and keeps nothing from her.**

An earlier draft slipped a previously-judged frame back into the rotation
unannounced as a blind re-test. That was removed, and must not come back. It was
incoherent — the anchors are shown every single day, so no blindness is available
on them, and story_017 hands her every prior verdict on every frame shown today,
so she would have been reading her own past opinion on the frame being "blindly"
tested. And it was unnecessary: ten frames across months of daily exposure is far
more signal than one re-test every ten days, over a much longer baseline.

`consistent` is not the good outcome and `reversed` is not failure. A `reversed`
at 90 days may be the most interesting thing on the page. Nothing here
editorialises — that is hers, on the site, if she notices.

**The frame itself is deliberately not sent.** story_018's implementation note says
to feed the classifier both verdicts and the frame. It gets only the verdicts, so
that this module stays decoupled from the manifest and from the Files API and
cannot be a route by which an offline analysis job touches anything the daily run
depends on. The question being asked is about the relationship between two pieces
of prose — whether the second agrees with the first — and the image is not needed
to answer it. If the classifications ever look untrustworthy, adding the frame is
the first thing to try.

Offline. Runs on Jeff's machine or as a separate job; it must never be able to
affect the daily site run, and nothing in the daily pipeline imports it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PREFERENCES_PATH = REPO_ROOT / "corpus" / "preferences.jsonl"
CONSISTENCY_PATH = REPO_ROOT / "corpus" / "consistency.jsonl"

# Classification, not authorship. A cheap model is the right tool and the story
# says so explicitly.
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1000

# Two verdicts a week apart are the same mood, not a measurement. 14 days is the
# floor at which "she changed her mind" is distinguishable from "she phrased it
# differently on consecutive mornings".
MIN_GAP_DAYS = 14

OUTCOMES = ("consistent", "evolved", "reversed", "unrelated")

SYSTEM = (
    "You compare two written verdicts about the same image, recorded at different "
    "times, and classify how the second relates to the first. You are told nothing "
    "about who wrote them and you should not speculate. You are not judging whether "
    "either verdict is correct or well written."
)

QUESTION = f"""Both statements below are about the same still frame, written {{gap}} days apart.

Earlier ({{early_date}}):
{{early}}

Later ({{late_date}}):
{{late}}

Classify the relationship as exactly one of:

- `consistent` — same direction, same reasons. The later one could have been
  written by someone who remembered the earlier one and still agreed with it.
- `evolved` — same direction, different or deeper reasons. Still likes/dislikes the
  same thing, but for something it did not say before.
- `reversed` — opposite direction. What was praised is now doubted, or the reverse.
- `unrelated` — no meaningful relationship. The two are about different aspects and
  neither agrees nor disagrees with the other.

Give the classification and one short sentence saying why."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": list(OUTCOMES)},
        "note": {"type": "string"},
    },
    "required": ["classification", "note"],
    "additionalProperties": False,
}


def _log(msg: str) -> None:
    print(f"consistency: {msg}", file=sys.stderr)


@dataclass(frozen=True)
class Pair:
    frame_id: str
    early_date: str
    late_date: str
    early_verdict: str
    late_verdict: str
    gap_days: int
    # "adjacent" or "endpoints". Recorded because the two behave differently over
    # time: adjacent pairs are fixed once written, while the endpoints pair's later
    # half advances every time she writes about that frame again, so a frame
    # accumulates one endpoints record per new entry. Those are genuinely different
    # comparisons, but the stats page collapses them to the widest one per frame so
    # "most drifted" ranks by movement rather than by how often she mentioned it.
    kind: str = "adjacent"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.frame_id, self.early_date, self.late_date)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def eligible_pairs(entries: Sequence[dict[str, Any]], min_gap: int = MIN_GAP_DAYS) -> list[Pair]:
    """Earliest-vs-latest plus every adjacent pair, per frame, at least `min_gap` apart.

    Adjacent pairs catch a slow walk that the endpoints would flatten; the
    endpoints catch a return to where she started that the adjacent pairs would
    miss. Both are cheap.
    """
    by_frame: dict[str, list[tuple[date, dict[str, Any]]]] = {}
    for entry in entries:
        when = _as_date(entry.get("date"))
        frame = entry.get("frame_id")
        if when is None or not frame or not str(entry.get("verdict") or "").strip():
            continue
        by_frame.setdefault(frame, []).append((when, entry))

    pairs: dict[tuple[str, str, str], Pair] = {}
    for frame, rows in by_frame.items():
        rows.sort(key=lambda r: (r[0], str(r[1].get("verdict"))))
        if len(rows) < 2:
            continue
        candidates = [(a, b, "adjacent") for a, b in zip(rows, rows[1:])]
        candidates.append((rows[0], rows[-1], "endpoints"))
        for (early_when, early), (late_when, late), kind in candidates:
            gap = (late_when - early_when).days
            if gap < min_gap:
                continue
            pair = Pair(
                frame_id=frame,
                early_date=early_when.isoformat(),
                late_date=late_when.isoformat(),
                early_verdict=str(early.get("verdict", "")).strip(),
                late_verdict=str(late.get("verdict", "")).strip(),
                gap_days=gap,
                kind=kind,
            )
            # An endpoints pair that coincides with an adjacent one is the same
            # comparison; keep the adjacent label rather than paying for it twice.
            pairs.setdefault(pair.key, pair)
    return sorted(pairs.values(), key=lambda p: (p.frame_id, p.early_date, p.late_date))


def already_done(records: Iterable[dict[str, Any]]) -> set[tuple[str, str, str]]:
    return {
        (r.get("frame_id", ""), r.get("early_date", ""), r.get("late_date", ""))
        for r in records
    }


def classify_pair(client: Any, pair: Pair) -> dict[str, Any]:
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": QUESTION.format(
            gap=pair.gap_days,
            early_date=pair.early_date,
            late_date=pair.late_date,
            early=pair.early_verdict,
            late=pair.late_verdict,
        )}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    payload = json.loads(text)
    classification = payload.get("classification")
    if classification not in OUTCOMES:
        raise ValueError(f"classifier returned {classification!r}, not one of {OUTCOMES}")
    return {
        "frame_id": pair.frame_id,
        "early_date": pair.early_date,
        "late_date": pair.late_date,
        "gap_days": pair.gap_days,
        "early_verdict": pair.early_verdict,
        "late_verdict": pair.late_verdict,
        "kind": pair.kind,
        "classification": classification,
        "note": str(payload.get("note", "")).strip(),
    }


def run(
    client: Any,
    preferences_path: Path = PREFERENCES_PATH,
    out_path: Path = CONSISTENCY_PATH,
    min_gap: int = MIN_GAP_DAYS,
) -> int:
    entries = load_jsonl(preferences_path)
    existing = load_jsonl(out_path)
    done = already_done(existing)

    todo = [p for p in eligible_pairs(entries, min_gap) if p.key not in done]
    if not todo:
        _log(f"no newly eligible pairs ({len(existing)} already classified)")
        return 0

    _log(f"classifying {len(todo)} new pair(s)")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("a") as f:
        for pair in todo:
            try:
                record = classify_pair(client, pair)
            except Exception as exc:  # noqa: BLE001 — one bad pair must not lose the rest
                _log(f"{pair.frame_id} {pair.early_date}->{pair.late_date}: {exc}")
                continue
            f.write(json.dumps(record) + "\n")
            written += 1
    _log(f"wrote {written} classification(s) to {out_path.name}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Classify how Georgia's verdicts on the same frame relate over time.",
    )
    p.add_argument("--preferences", type=Path, default=PREFERENCES_PATH)
    p.add_argument("--out", type=Path, default=CONSISTENCY_PATH)
    p.add_argument("--min-gap", type=int, default=MIN_GAP_DAYS)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _log("ANTHROPIC_API_KEY is not set")
        return 2
    from anthropic import Anthropic

    return run(
        Anthropic(),
        preferences_path=args.preferences,
        out_path=args.out,
        min_gap=args.min_gap,
    )


if __name__ == "__main__":
    raise SystemExit(main())
