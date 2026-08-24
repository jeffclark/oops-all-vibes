"""Upload the keepers once, and write the public, reproducible manifest.

This is the story that keeps copyrighted pixels out of a public repo while
leaving the corpus fully verifiable by a stranger. The frames live in the
Anthropic Files API; the repo holds only ids and provenance. `url` + `t` is
enough for anyone to regenerate the exact frame from the original video, and
that is a correctness property, not a nicety — it is the whole reason the
archive can be public.

Offline tooling. The daily run reads `corpus/manifest.json`; it never runs this.

**On verification.** Story_015 specified "the batch `ids[]` lookup (up to 100 ids
in one request)". No such parameter exists on `GET /v1/files` or on the SDK's
`client.beta.files.list()`, which takes only `after_id`/`before_id`/`limit`. The
property the story was actually buying — one cheap sweep for the whole manifest
rather than one request per frame, so routine verification stays affordable
enough to run every morning — is met by listing the workspace once and checking
membership. That is O(pages), not O(frames): ~210 corpus files is a couple of
pages, against 210 individual lookups.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from scripts.corpus.ingest import RAW_ROOT, REPO_ROOT, IngestError, load_sources

CURATION_DIR = REPO_ROOT / "corpus" / "curation"
MANIFEST_PATH = REPO_ROOT / "corpus" / "manifest.json"

# Written by --verify-only so the stats page can surface a corpus that is quietly
# rotting. The daily run never reads it; nothing here can affect the site.
VERIFY_STATUS_PATH = REPO_ROOT / "corpus" / "verify.json"

FILES_BETA = "files-api-2025-04-14"
MANIFEST_VERSION = 1

# 10 frames shown every single day, forever. Repetition is the mechanism — a
# preference cannot form from a single viewing.
ANCHOR_TARGET = 10

# Page size for the liveness sweep. The API clamps this itself; the SDK
# paginates past it either way.
LIST_PAGE_SIZE = 1000


class PublishError(Exception):
    """Anything that should stop a publish with a message a human can act on."""


def _log(msg: str) -> None:
    print(f"publish: {msg}", file=sys.stderr)


def frame_id(show_id: str, t: int) -> str:
    return f"{show_id}-t{t}"


# ------------------------------------------------------------------- loading


def load_curations(curation_dir: Path = CURATION_DIR) -> dict[str, dict[str, Any]]:
    """show_id -> curation, for every committed curation file."""
    if not curation_dir.is_dir():
        raise PublishError(f"no curation directory at {curation_dir} — run story_014 first")
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(curation_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise PublishError(f"{path.name} is not valid JSON: {exc}") from None
        show_id = data.get("show_id") or path.stem
        keepers = data.get("keepers")
        if not isinstance(keepers, list) or not keepers:
            raise PublishError(f"{path.name} has no keepers")
        out[show_id] = data
    if not out:
        raise PublishError(f"no curation files in {curation_dir} — run story_014 first")
    return out


def source_index(sources: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {e["show_id"]: e for e in sources}


# -------------------------------------------------------------- anchor rules


def rarity_scores(shows: Sequence[str], tags_by_show: dict[str, Sequence[str]]) -> dict[str, float]:
    """Sum over a show's axis_tags of 1 / (shows carrying that tag). Higher = rarer.

    Counted over the published corpus rather than over sources.json, so the score
    describes the shelf that actually exists rather than one that might later.
    """
    counts: dict[str, int] = {}
    for show in shows:
        for tag in set(tags_by_show.get(show, ())):
            counts[tag] = counts.get(tag, 0) + 1
    return {
        show: sum(1.0 / counts[tag] for tag in set(tags_by_show.get(show, ())))
        for show in shows
    }


def order_shows(shows: Sequence[str], tags_by_show: dict[str, Sequence[str]]) -> list[str]:
    """Rarest axis tags first, `show_id` ascending on ties.

    The tiebreak is load-bearing. With 19 shows most `org:` tags are unique, so a
    lot of shows score identically, and without an explicit tiebreak the anchor set
    would depend on the order entries happen to sit in sources.json — the same
    corpus could publish two different shelves.
    """
    scores = rarity_scores(shows, tags_by_show)
    return sorted(shows, key=lambda s: (-scores[s], s))


def assign_anchors(
    curations: dict[str, dict[str, Any]],
    order: Sequence[str],
    target: int = ANCHOR_TARGET,
) -> list[str]:
    """Anchor frame_ids, filled in passes so the rule is total for any show count.

    Pass 1 takes every show's rank-1 keeper in rarity order, pass 2 every show's
    rank-2, and so on until `target` is reached or the keepers run out. Diversity
    across shows outranks a show's own ranking, so no show contributes a third
    frame while any show has contributed fewer than two — the pass structure gives
    that for free rather than by a separate check.
    """
    # From the rank values, not from how many keepers there are: a curation file
    # with non-contiguous ranks would otherwise stop the passes early and quietly
    # under-fill the anchor set.
    deepest = max(
        (int(k.get("rank") or 0) for c in curations.values() for k in (c.get("keepers") or [])),
        default=0,
    )
    anchors: list[str] = []
    for rank in range(1, deepest + 1):
        for show_id in order:
            keepers = (curations.get(show_id) or {}).get("keepers") or []
            keeper = next((k for k in keepers if k.get("rank") == rank), None)
            if keeper is None:
                continue
            anchors.append(frame_id(show_id, int(keeper["t"])))
            if len(anchors) >= target:
                return anchors
    return anchors


# ------------------------------------------------------------------- uploads


def upload(client: Any, path: Path, media_type: str) -> str:
    with path.open("rb") as fh:
        result = client.beta.files.upload(
            file=(path.name, fh, media_type), betas=[FILES_BETA]
        )
    return result.id


def live_file_ids(client: Any) -> set[str]:
    """Every file id the workspace currently holds, in one paginated sweep.

    See the module docstring: this is the honest implementation of story_015's
    "batch lookup", because the parameter that story assumed does not exist.
    """
    return {f.id for f in client.beta.files.list(limit=LIST_PAGE_SIZE, betas=[FILES_BETA])}


# ------------------------------------------------------------------ manifest


def read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        _log(f"{path.name} is unparseable; treating it as absent and republishing")
        return None


def manifest_bytes(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2) + "\n"


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    """Temp file then rename, so a crash can never leave a half-written manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(manifest_bytes(payload))
    tmp.replace(path)


def corpus_changed(old: dict[str, Any] | None, frames: list[dict], shapes: list[dict]) -> bool:
    """Did anything about the shelf actually move?

    `published_at` is the one field that must not churn: a re-publish that uploads
    nothing has to produce a byte-identical file, or the git history fills with
    commits that say a date changed and nothing else.
    """
    if old is None:
        return True
    return old.get("frames") != frames or old.get("shapes") != shapes


def build_manifest(
    curations: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    file_ids: dict[str, str],
    shape_ids: dict[str, str],
    run_date: date,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shows = sorted(curations)
    tags_by_show = {s: list(sources[s].get("axis_tags") or []) for s in shows}
    anchors = set(assign_anchors(curations, order_shows(shows, tags_by_show)))

    frames: list[dict[str, Any]] = []
    for show_id in shows:
        entry = sources[show_id]
        for keeper in sorted(curations[show_id]["keepers"], key=lambda k: k["rank"]):
            t = int(keeper["t"])
            fid = frame_id(show_id, t)
            frames.append({
                "frame_id": fid,
                "file_id": file_ids[fid],
                "show_id": show_id,
                "corps": entry["corps"],
                "year": entry["year"],
                "t": t,
                "url": entry["url"],
                "axis_tags": list(entry.get("axis_tags") or []),
                "role": "anchor" if fid in anchors else "rotating",
                "curation_rank": int(keeper["rank"]),
            })

    shapes = [{"show_id": s, "file_id": shape_ids[s]} for s in shows if s in shape_ids]

    published_at = (
        run_date.isoformat()
        if corpus_changed(previous, frames, shapes)
        else previous.get("published_at", run_date.isoformat())
    )
    return {
        "version": MANIFEST_VERSION,
        "published_at": published_at,
        "frames": frames,
        "shapes": shapes,
    }


# ---------------------------------------------------------------- publishing


def publish(
    client: Any,
    curation_dir: Path = CURATION_DIR,
    raw_root: Path = RAW_ROOT,
    manifest_path: Path = MANIFEST_PATH,
    sources_path: Path | None = None,
    run_date: date | None = None,
) -> dict[str, Any]:
    run_date = run_date or datetime.now(timezone.utc).date()
    curations = load_curations(curation_dir)
    entries = load_sources(sources_path) if sources_path else load_sources()
    sources = source_index(entries)

    unknown = [s for s in curations if s not in sources]
    if unknown:
        raise PublishError(f"curated show(s) not in sources.json: {', '.join(sorted(unknown))}")

    previous = read_manifest(manifest_path)
    live = live_file_ids(client)

    known_frames = {f["frame_id"]: f["file_id"] for f in (previous or {}).get("frames", [])}
    known_shapes = {s["show_id"]: s["file_id"] for s in (previous or {}).get("shapes", [])}

    file_ids: dict[str, str] = {}
    shape_ids: dict[str, str] = {}
    uploaded = 0

    for show_id in sorted(curations):
        show_dir = raw_root / show_id
        for keeper in curations[show_id]["keepers"]:
            t = int(keeper["t"])
            fid = frame_id(show_id, t)
            existing = known_frames.get(fid)
            if existing and existing in live:
                file_ids[fid] = existing
                continue
            path = show_dir / "frames" / f"t{t:05d}.jpg"
            if not path.exists():
                raise PublishError(
                    f"{fid}: {path} is missing. Re-run ingest for {show_id} before publishing."
                )
            file_ids[fid] = upload(client, path, "image/jpeg")
            uploaded += 1

        existing_shape = known_shapes.get(show_id)
        if existing_shape and existing_shape in live:
            shape_ids[show_id] = existing_shape
            continue
        shape = show_dir / "shape.png"
        if not shape.exists():
            raise PublishError(f"{show_id}: shape.png is missing. Re-run ingest for {show_id}.")
        shape_ids[show_id] = upload(client, shape, "image/png")
        uploaded += 1

    payload = build_manifest(
        curations, sources, file_ids, shape_ids, run_date, previous=previous
    )

    # Verify before writing. A dead file_id fails a Messages request *before*
    # inference, so a manifest with one in it would cost a whole day of site.
    if uploaded:
        live = live_file_ids(client)
    missing = check_manifest(payload, live)
    if missing:
        raise PublishError(
            "not writing a manifest — these file_ids do not resolve: "
            + ", ".join(missing[:10])
        )

    write_manifest(manifest_path, payload)
    anchors = sum(1 for f in payload["frames"] if f["role"] == "anchor")
    _log(
        f"{len(payload['frames'])} frames ({anchors} anchors) across {len(curations)} show(s), "
        f"{len(payload['shapes'])} shape plot(s); {uploaded} newly uploaded"
    )
    return payload


def manifest_file_ids(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, file_id) for everything the manifest references."""
    out = [(f["frame_id"], f["file_id"]) for f in payload.get("frames", [])]
    out += [(f"{s['show_id']}/shape", s["file_id"]) for s in payload.get("shapes", [])]
    return out


def check_manifest(payload: dict[str, Any], live: set[str]) -> list[str]:
    return [label for label, fid in manifest_file_ids(payload) if fid not in live]


def write_verify_status(
    path: Path,
    ok: bool,
    missing: Sequence[str],
    checked_at: date,
    *,
    failed: bool | None = None,
    reason: str = "",
) -> None:
    """Record what the last verification found.

    `failed` is separate from `not ok` on purpose: "there is no manifest yet" and
    "the check could not run" are both not-ok, but neither is the thing
    corpus_verify_failed is meant to shout about, which is frames that used to
    resolve and have stopped.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "checked_at": checked_at.isoformat(),
        "ok": ok,
        "corpus_verify_failed": (not ok) if failed is None else failed,
        "missing": list(missing),
        "reason": reason,
    }, indent=2) + "\n")
    tmp.replace(path)


def verify_only(
    client: Any,
    manifest_path: Path = MANIFEST_PATH,
    status_path: Path = VERIFY_STATUS_PATH,
    run_date: date | None = None,
) -> int:
    run_date = run_date or datetime.now(timezone.utc).date()
    payload = read_manifest(manifest_path)
    if payload is None:
        _log(f"no manifest at {manifest_path} — nothing to verify")
        print("::warning title=Corpus::no corpus manifest to verify")
        write_verify_status(
            status_path, False, [], run_date,
            failed=False, reason="no corpus published yet",
        )
        return 1

    try:
        live = live_file_ids(client)
    except Exception as exc:  # noqa: BLE001 — a check that did not run is not a pass
        # Without this the previous run's "everything resolves" line stays on the
        # stats page and a corpus could rot behind a stale green light.
        _log(f"verification did not complete ({exc})")
        print(f"::warning title=Corpus::corpus verification did not run: {exc}")
        write_verify_status(
            status_path, False, [], run_date,
            failed=False, reason=f"check did not complete: {exc}",
        )
        return 1

    missing = check_manifest(payload, live)
    write_verify_status(status_path, not missing, missing, run_date)
    if missing:
        named = ", ".join(missing)
        _log(f"{len(missing)} dead file reference(s): {named}")
        print(f"::warning title=Corpus::{len(missing)} corpus file_id(s) no longer resolve: {named}")
        return 1
    _log(f"all {len(manifest_file_ids(payload))} file_id(s) resolve")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Upload curated keepers and write corpus/manifest.json (offline).",
    )
    p.add_argument(
        "--verify-only",
        action="store_true",
        help="check every file_id in the current manifest still resolves; no uploads",
    )
    p.add_argument("--curation-dir", type=Path, default=CURATION_DIR)
    p.add_argument("--raw", type=Path, default=RAW_ROOT)
    p.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    p.add_argument("--status", type=Path, default=VERIFY_STATUS_PATH)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _log("ANTHROPIC_API_KEY is not set")
        return 2

    from anthropic import Anthropic

    client = Anthropic()
    try:
        if args.verify_only:
            return verify_only(client, manifest_path=args.manifest, status_path=args.status)
        publish(
            client,
            curation_dir=args.curation_dir,
            raw_root=args.raw,
            manifest_path=args.manifest,
        )
    except (PublishError, IngestError) as exc:
        _log(str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
