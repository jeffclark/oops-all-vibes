"""Curation session: Georgia picks her own shelf.

Offline, one show at a time, run on Jeff's machine. Never in CI, never in the
3am pipeline.

Georgia asked for "a corpus I can have taste about" — a finite set she sees
repeatedly, so preference can form from being moved by specific things rather
than from reading descriptions of people being moved. The whole exercise only
works if the set is *hers*. So this script does not rank, score, filter or
second-guess her; it shows her the candidates, holds her to a hard cap, and
writes down what she said.

Two rounds, because the cap is the mechanism:

- **Round 1** shows the field contact sheets and asks for exactly 25 timestamps.
- **Round 2** shows those 25 as full frames and asks for exactly 10, ranked, with
  reasons.

Forced choice under a cap is the only thing separating taste from appreciation.
If she could keep everything she liked, she would only be demonstrating that she
can tell good from bad, which is not the same as preferring one good thing over
another. Both counts are enforced in the response schema and re-checked here.

Two things are deliberately withheld:

- **The diary history.** This is a fresh act of looking, not a continuation of her
  running narrative.
- **The corps and the year.** She reacts to the image, not to a reputation. The
  frames themselves sometimes give it away — a title card, a banner, a uniform —
  and that is fine and unavoidable. What is avoidable is the caption burned into
  `shape.png` by ingest, which names the corps and year outright, so it is masked
  out of the copy sent here.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from scripts.call_model import FALLBACK_BETA, MODEL
from scripts.corpus.ingest import RAW_ROOT, REPO_ROOT, IngestError, _log

CURATION_DIR = REPO_ROOT / "corpus" / "curation"
SOUL_PATH = REPO_ROOT / "georgia-soul.md"

SHORTLIST_N = 25
KEEPERS_N = 10

# Reasons and a show statement, not an essay. 8000 leaves room for adaptive
# thinking, which shares this budget on Opus 5.
MAX_TOKENS = 8000

# The caption ingest burns into the top-left of shape.png is "<corps> <year>".
# Painted over rather than cropped, so the image she sees is still the 1024x384
# plot with its axes where she expects them. The band is comfortably above the
# axes — the tempo trace tops out near y=45 and the loudness fill lower still —
# so nothing but background and the caption is inside it.
SHAPE_TITLE_BOX = (0, 0, 560, 30)
SHAPE_BG = (17, 17, 17)

USD_PER_MTOK_IN = 5.0
USD_PER_MTOK_OUT = 25.0


class CurationError(Exception):
    """Anything that should stop a show's curation with an actionable message."""


@dataclass
class Keeper:
    rank: int
    t: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"rank": self.rank, "t": self.t, "reason": self.reason}


@dataclass
class Curation:
    show_id: str
    curated_at: str
    show_statement: str
    shortlist: list[int]
    keepers: list[Keeper]

    def to_dict(self) -> dict[str, Any]:
        return {
            "show_id": self.show_id,
            "curated_at": self.curated_at,
            "show_statement": self.show_statement,
            "shortlist": self.shortlist,
            "keepers": [k.to_dict() for k in self.keepers],
        }


# ----------------------------------------------------------------- schemas
#
# Structured outputs enforce the *shape*: an object with exactly these keys, the
# right types, nothing extra. That removes the whole class of "parsed prose and
# hoped" failures.
#
# They cannot enforce the *counts*. story_014 assumed they could, but the API's
# constrained decoding rejects `minItems` above 1 outright and rejects `maxItems`
# entirely — verified against both `output_config.format` and strict tool use:
#
#     For 'array' type, 'minItems' values other than 0 or 1 are not supported
#     For 'array' type, property 'maxItems' is not supported
#
# So the cap — the thing this whole story exists to impose — is enforced here
# instead, by `validate_shortlist`/`validate_keepers` plus the one retry the story
# also asks for. The guarantee is the same at the point it matters: no curation
# file is ever written with the wrong number of picks in it.

SHORTLIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "shortlist": {"type": "array", "items": {"type": "integer"}, "minItems": 1}
    },
    "required": ["shortlist"],
    "additionalProperties": False,
}

KEEPERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "show_statement": {"type": "string"},
        "keepers": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "rank": {"type": "integer"},
                    "t": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["rank", "t", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["show_statement", "keepers"],
    "additionalProperties": False,
}


# ------------------------------------------------------------------ prompts

ROUND_1_TASK = f"""This is not a site day. Nobody is waiting for HTML.

You asked for a corpus you could have taste about — a small fixed set of images you
see again and again, so that preference can come from being moved by specific
things instead of from reading about people being moved. This is you building it.

Above are contact sheets of candidate stills from one marching band show, taken at
a fixed interval from the recording, in order. Every cell is labelled with its
timestamp in seconds. Also above is the shape of the show's sound over time —
loudness as the filled trace, tempo as the line, and a tick on the x-axis at every
candidate. You cannot hear it. That plot is the whole of what you get.

Shortlist exactly {SHORTLIST_N} timestamps: the ones you want to look at properly, at
full size, before deciding what you keep.

You are not being asked which are competent. Nearly all of them are competent.
You are being asked which ones you want to see again. If a form does nothing for
you, leave it, however well drilled it is.

Answer with the {SHORTLIST_N} timestamps as integers — the number in the `t=` label,
not the minutes:seconds. Exactly {SHORTLIST_N}, no duplicates, and only timestamps
that actually appear on a sheet above."""

ROUND_2_TASK = f"""Here are the {SHORTLIST_N} you shortlisted, at full size and in time
order, each preceded by its timestamp. The sound shape is here again.

Keep exactly {KEEPERS_N}. Rank them 1 to {KEEPERS_N}, 1 being the one you would keep if
you could only keep one.

For each, write a reason. Not a description — a verdict. Say what it does, in a way
a stranger could read and disagree with. "Symmetric block with the pit at the front
sideline" is a description and is worthless to you later. "The gap where the form
hasn't closed yet is better than the form" is a verdict.

Then write a short statement about what this show as a whole is doing — the thing
it is after, whether or not it gets there.

The cap will bite. Say so in the statement: name what you are sorry to lose out of
the {SHORTLIST_N} and what the cap took from you. Which ones you drop is as much the
record as which ones you keep.

Use the timestamps exactly as labelled above."""


# -------------------------------------------------------------------- inputs


def load_ingest(show_dir: Path) -> dict[str, Any]:
    path = show_dir / "ingest.json"
    if not path.exists():
        raise CurationError(
            f"no ingest.json in {show_dir}. Run "
            f"`python -m scripts.corpus.ingest --show {show_dir.name}` first."
        )
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise CurationError(f"{path} is not valid JSON: {exc}") from None


def field_sheets(show_dir: Path) -> list[Path]:
    return sorted((show_dir / "sheets").glob("field_*.jpg"))


def check_ready(show_dir: Path, data: dict[str, Any]) -> list[int]:
    """Refuse to curate a show that isn't ready, before spending anything.

    Sheet generation lives in ingest precisely so a human can look before this
    runs; building them here on demand would quietly remove that gate.
    """
    if not (show_dir / "sheets").is_dir():
        raise CurationError(
            f"{show_dir.name}: no sheets/ directory. Curation reads the sheets ingest "
            f"builds — run `python -m scripts.corpus.ingest --show {show_dir.name}` "
            "and look at them before curating."
        )
    shown = [int(t) for t in (data.get("field_times") or [])]
    if not shown:
        raise CurationError(
            f"{show_dir.name}: no frame was judged a field shot, so the field_* sheets "
            "hold every candidate rather than the drill ones. Do not curate this — the "
            "other_* frames are category noise and sorting them is not a preference. "
            "Fix the show at story_013 first: check the angle in sources.json, try a "
            f"denser interval_s, or run `python -m scripts.corpus.classify --show "
            f"{show_dir.name}` and ingest again."
        )
    if not field_sheets(show_dir):
        raise CurationError(
            f"{show_dir.name}: ingest.json reports {len(shown)} field frames but no "
            "field_* sheet is on disk. Re-run ingest for this show."
        )
    return shown


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


def _image_block(data: bytes, media_type: str = "image/jpeg") -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": _b64(data)},
    }


def anonymised_shape(shape_png: Path) -> bytes:
    """shape.png with the "<corps> <year>" caption painted out.

    Round 1 must not tell her whose show this is, and ingest writes the corps and
    year into the plot. Masking here rather than changing ingest keeps the archived
    artifact honest — the plot on disk still says what it is — while the copy she
    sees in a blind round does not.
    """
    from PIL import Image, ImageDraw

    with Image.open(shape_png) as im:
        out = im.convert("RGB")
        ImageDraw.Draw(out).rectangle(SHAPE_TITLE_BOX, fill=SHAPE_BG)
        buf = io.BytesIO()
        out.save(buf, format="PNG")
    return buf.getvalue()


def build_round_1(show_dir: Path) -> list[dict[str, Any]]:
    sheets = field_sheets(show_dir)
    content: list[dict[str, Any]] = []
    for n, sheet in enumerate(sheets, start=1):
        content.append({"type": "text", "text": f"Contact sheet {n} of {len(sheets)}"})
        content.append(_image_block(sheet.read_bytes()))
    content.append({"type": "text", "text": "The shape of the show's sound:"})
    content.append(_image_block(anonymised_shape(show_dir / "shape.png"), "image/png"))
    content.append({"type": "text", "text": ROUND_1_TASK})
    return content


def build_round_2(show_dir: Path, times: Sequence[int]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for t in times:
        content.append({"type": "text", "text": f"t={t}"})
        content.append(_image_block((show_dir / "frames" / f"t{t:05d}.jpg").read_bytes()))
    content.append({"type": "text", "text": "The shape of the show's sound, again:"})
    content.append(_image_block(anonymised_shape(show_dir / "shape.png"), "image/png"))
    content.append({"type": "text", "text": ROUND_2_TASK})
    return content


# --------------------------------------------------------------------- call


@dataclass
class Spend:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def usd(self) -> float:
        return (
            self.input_tokens * USD_PER_MTOK_IN + self.output_tokens * USD_PER_MTOK_OUT
        ) / 1_000_000

    def add(self, usage: Any) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0


def ask(
    client: Any,
    system: str,
    content: list[dict[str, Any]],
    schema: dict[str, Any],
    spend: Spend,
    correction: str | None = None,
    prior: str | None = None,
) -> dict[str, Any]:
    """One structured call. `prior`/`correction` replay a rejected answer for a retry.

    Handing the model its own bad answer back, with what was wrong with it, does
    better than re-asking the original question cold — it can see the mistake.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
    if prior is not None and correction is not None:
        messages.append({"role": "assistant", "content": [{"type": "text", "text": prior}]})
        messages.append({"role": "user", "content": [{"type": "text", "text": correction}]})

    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        system=system,
        messages=messages,
        output_config={"format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        message = stream.get_final_message()

    spend.add(message.usage)
    if message.stop_reason == "refusal":
        raise CurationError(
            "the request was declined by safety classifiers and the fallback chain "
            "did not recover it"
        )
    text = "".join(b.text for b in message.content if b.type == "text")
    if not text.strip():
        raise CurationError("model returned no text content")
    return {"text": text}


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CurationError(f"model output was not valid JSON: {exc}") from None


# ---------------------------------------------------------------- validation


def validate_shortlist(payload: Any, shown: Sequence[int]) -> list[int]:
    if not isinstance(payload, dict):
        raise CurationError("round 1: expected a JSON object")
    picks = payload.get("shortlist")
    if not isinstance(picks, list) or not all(isinstance(t, int) and not isinstance(t, bool) for t in picks):
        raise CurationError("round 1: `shortlist` must be a list of integers")
    if len(picks) != SHORTLIST_N:
        raise CurationError(f"round 1: got {len(picks)} timestamps, need exactly {SHORTLIST_N}")
    if len(set(picks)) != len(picks):
        dupes = sorted({t for t in picks if picks.count(t) > 1})
        raise CurationError(f"round 1: duplicate timestamps {dupes}")
    unknown = [t for t in picks if t not in set(shown)]
    if unknown:
        raise CurationError(
            f"round 1: {len(unknown)} timestamp(s) are not on any sheet you were "
            f"shown: {unknown[:8]}"
        )
    return list(picks)


def validate_keepers(payload: Any, shortlist: Sequence[int]) -> tuple[str, list[Keeper]]:
    if not isinstance(payload, dict):
        raise CurationError("round 2: expected a JSON object")
    statement = str(payload.get("show_statement") or "").strip()
    if not statement:
        raise CurationError("round 2: `show_statement` is empty")

    rows = payload.get("keepers")
    if not isinstance(rows, list):
        raise CurationError("round 2: `keepers` must be a list")
    if len(rows) != KEEPERS_N:
        raise CurationError(f"round 2: got {len(rows)} keepers, need exactly {KEEPERS_N}")

    keepers: list[Keeper] = []
    for row in rows:
        if not isinstance(row, dict):
            raise CurationError("round 2: every keeper must be an object")
        rank, t = row.get("rank"), row.get("t")
        reason = str(row.get("reason") or "").strip()
        if not isinstance(rank, int) or isinstance(rank, bool):
            raise CurationError(f"round 2: rank must be an integer, got {rank!r}")
        if not isinstance(t, int) or isinstance(t, bool):
            raise CurationError(f"round 2: t must be an integer, got {t!r}")
        if not reason:
            raise CurationError(f"round 2: keeper t={t} has an empty reason")
        keepers.append(Keeper(rank=rank, t=t, reason=reason))

    ranks = sorted(k.rank for k in keepers)
    if ranks != list(range(1, KEEPERS_N + 1)):
        raise CurationError(f"round 2: ranks must be 1-{KEEPERS_N} exactly once each, got {ranks}")

    times = [k.t for k in keepers]
    if len(set(times)) != len(times):
        raise CurationError("round 2: the same timestamp was kept twice")
    unknown = [t for t in times if t not in set(shortlist)]
    if unknown:
        raise CurationError(
            f"round 2: timestamp(s) {unknown} were not in the {SHORTLIST_N} you shortlisted"
        )

    keepers.sort(key=lambda k: k.rank)
    return statement, keepers


# ------------------------------------------------------------- orchestration


def run_round(
    client: Any,
    system: str,
    content: list[dict[str, Any]],
    schema: dict[str, Any],
    validate,
    spend: Spend,
    label: str,
) -> Any:
    """Ask, validate, and on a bad answer show it back once before giving up."""
    result = ask(client, system, content, schema, spend)
    try:
        return validate(parse_json(result["text"]))
    except CurationError as first:
        _log(f"{label}: {first} — retrying once")
        correction = (
            f"That answer was rejected: {first}\n\n"
            "Answer again, in the same format, fixing exactly that. Do not explain "
            "the mistake; just give the corrected answer."
        )
        retry = ask(
            client, system, content, schema, spend,
            correction=correction, prior=result["text"],
        )
        try:
            return validate(parse_json(retry["text"]))
        except CurationError as second:
            raise CurationError(f"{label} failed twice: {second}") from None


def curate_show(
    show_id: str,
    out_root: Path = RAW_ROOT,
    curation_dir: Path = CURATION_DIR,
    client: Any | None = None,
    force: bool = False,
) -> Curation:
    dest = curation_dir / f"{show_id}.json"
    if dest.exists() and not force:
        raise CurationError(
            f"{show_id}: {dest} already exists. Her picks are not something to redo "
            "casually — pass --force if you really mean to replace them."
        )

    show_dir = out_root / show_id
    data = load_ingest(show_dir)
    shown = check_ready(show_dir, data)
    if not (show_dir / "shape.png").exists():
        raise CurationError(f"{show_id}: shape.png is missing — re-run ingest.")

    system = SOUL_PATH.read_text()
    spend = Spend()

    if client is None:
        from anthropic import Anthropic

        client = Anthropic()

    _log(f"{show_id}: round 1 — {len(field_sheets(show_dir))} field sheet(s), {len(shown)} candidates")
    shortlist = run_round(
        client, system, build_round_1(show_dir), SHORTLIST_SCHEMA,
        lambda p: validate_shortlist(p, shown), spend, f"{show_id} round 1",
    )
    shortlist = sorted(shortlist)

    missing = [t for t in shortlist if not (show_dir / "frames" / f"t{t:05d}.jpg").exists()]
    if missing:
        raise CurationError(f"{show_id}: shortlisted frames not on disk: {missing}")

    _log(f"{show_id}: round 2 — {len(shortlist)} frames at full size")
    statement, keepers = run_round(
        client, system, build_round_2(show_dir, shortlist), KEEPERS_SCHEMA,
        lambda p: validate_keepers(p, shortlist), spend, f"{show_id} round 2",
    )

    result = Curation(
        show_id=show_id,
        curated_at=date.today().isoformat(),
        show_statement=statement,
        shortlist=shortlist,
        keepers=keepers,
    )
    curation_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result.to_dict(), indent=2) + "\n")
    tmp.replace(dest)
    _log(
        f"{show_id}: kept {[k.t for k in keepers]} — "
        f"{spend.input_tokens:,} in / {spend.output_tokens:,} out, ${spend.usd:.2f}"
    )
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Curate one show's corpus keepers with Georgia (offline; not for CI).",
    )
    p.add_argument(
        "--show",
        help="show_id to curate. Required — curation is one deliberate act per show.",
    )
    p.add_argument("--force", action="store_true", help="replace an existing curation file")
    p.add_argument("--out", type=Path, default=RAW_ROOT)
    p.add_argument("--curation-dir", type=Path, default=CURATION_DIR)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.show:
        _log(
            "curation is per-show: pass --show <show_id>. There is no bulk mode on "
            "purpose — each show is a separate act of looking, and a bulk run would "
            "turn 19 of them into one unattended batch."
        )
        return 2
    try:
        curate_show(
            args.show,
            out_root=args.out,
            curation_dir=args.curation_dir,
            force=args.force,
        )
    except (CurationError, IngestError) as exc:
        _log(f"{args.show}: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
