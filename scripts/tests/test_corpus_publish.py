"""Tests for scripts/corpus/publish.py — story_015.

The Files API is faked at `client.beta.files`. Everything else is real: anchor
assignment, the rarity ordering and its tiebreak, idempotence, the atomic write,
and the byte-identical-re-publish property.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.corpus import publish
from scripts.corpus.publish import PublishError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_DATE = date(2026, 8, 25)


# ------------------------------------------------------------------ fixtures


class FakeFiles:
    def __init__(self, fail_after: int | None = None):
        self.store: set[str] = set()
        self.uploaded: list[str] = []
        self.list_calls = 0
        self.fail_after = fail_after
        self._n = 0

    def upload(self, file, betas=None):
        name, fh, media_type = file
        fh.read()
        self._n += 1
        if self.fail_after is not None and self._n > self.fail_after:
            raise RuntimeError("network went away mid-upload")
        fid = f"file_{self._n:04d}"
        self.store.add(fid)
        self.uploaded.append(name)
        return SimpleNamespace(id=fid)

    def list(self, limit=None, betas=None):
        self.list_calls += 1
        return [SimpleNamespace(id=i) for i in sorted(self.store)]


class FakeClient:
    def __init__(self, **kw):
        self.files = FakeFiles(**kw)
        self.beta = SimpleNamespace(files=self.files)


def source_entry(show_id, tags, year=2014):
    return {
        "show_id": show_id,
        "url": f"https://www.youtube.com/watch?v={show_id}xx",
        "corps": show_id.upper(),
        "year": year,
        "angle": "high",
        "axis_tags": list(tags),
    }


def make_corpus(tmp_path: Path, shows: dict[str, list[str]], keepers: int = 10):
    """Build sources.json, curation files and the raw frames they reference."""
    from PIL import Image

    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps([source_entry(s, t) for s, t in shows.items()]))

    curation_dir = tmp_path / "curation"
    curation_dir.mkdir()
    raw = tmp_path / "raw"
    for show_id in shows:
        times = [i * 8 for i in range(1, keepers + 1)]
        frames = raw / show_id / "frames"
        frames.mkdir(parents=True)
        for t in times:
            Image.new("RGB", (16, 9), (10, 20, 30)).save(frames / f"t{t:05d}.jpg")
        Image.new("RGB", (16, 6), (0, 0, 0)).save(raw / show_id / "shape.png")
        (curation_dir / f"{show_id}.json").write_text(json.dumps({
            "show_id": show_id,
            "curated_at": "2026-08-25",
            "show_statement": "a statement",
            "shortlist": times,
            "keepers": [
                {"rank": i + 1, "t": t, "reason": f"r{i}"} for i, t in enumerate(times)
            ],
        }))
    return sources_path, curation_dir, raw


def run_publish(tmp_path, client, sources_path, curation_dir, raw, **kw):
    return publish.publish(
        client,
        curation_dir=curation_dir,
        raw_root=raw,
        manifest_path=tmp_path / "manifest.json",
        sources_path=sources_path,
        run_date=kw.pop("run_date", RUN_DATE),
        **kw,
    )


def twelve_shows():
    # Each show gets a unique org tag plus shared era/class tags, which is the
    # real corpus's shape and the reason ties are the common case.
    return {
        f"show-{i:02d}": [f"org:o{i}", "class:dci-world", "era:2010s"] for i in range(1, 13)
    }


# --------------------------------------------------------------- happy path


def test_manifest_has_ten_frames_and_one_shape_per_show(tmp_path):
    shows = twelve_shows()
    sp, cd, raw = make_corpus(tmp_path, shows)
    client = FakeClient()
    payload = run_publish(tmp_path, client, sp, cd, raw)

    assert len(payload["frames"]) == 10 * len(shows)
    assert len(payload["shapes"]) == len(shows)
    assert {s["show_id"] for s in payload["shapes"]} == set(shows)


def test_every_frame_id_is_unique(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, twelve_shows())
    payload = run_publish(tmp_path, FakeClient(), sp, cd, raw)
    ids = [f["frame_id"] for f in payload["frames"]]
    assert len(set(ids)) == len(ids)


def test_every_entry_carries_provenance_a_stranger_could_use(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, twelve_shows())
    payload = run_publish(tmp_path, FakeClient(), sp, cd, raw)
    for f in payload["frames"]:
        assert f["url"].startswith("https://www.youtube.com/watch?v=")
        assert isinstance(f["t"], int) and not isinstance(f["t"], bool)
        assert f["corps"] and isinstance(f["year"], int)


def test_manifest_carries_no_image_data(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, twelve_shows())
    run_publish(tmp_path, FakeClient(), sp, cd, raw)
    text = (tmp_path / "manifest.json").read_text()
    assert "base64" not in text
    assert "data:image" not in text
    # a whole corpus of ids and provenance stays small
    assert len(text) < 200_000


# ------------------------------------------------------------------ anchors


def test_exactly_ten_anchors_with_a_full_corpus(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, twelve_shows())
    payload = run_publish(tmp_path, FakeClient(), sp, cd, raw)
    anchors = [f for f in payload["frames"] if f["role"] == "anchor"]
    assert len(anchors) == 10


def test_all_anchors_come_from_distinct_shows(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, twelve_shows())
    payload = run_publish(tmp_path, FakeClient(), sp, cd, raw)
    anchors = [f for f in payload["frames"] if f["role"] == "anchor"]
    assert len({f["show_id"] for f in anchors}) == 10
    assert all(f["curation_rank"] == 1 for f in anchors)


def test_reordering_sources_json_produces_the_identical_anchor_set(tmp_path):
    shows = twelve_shows()
    sp, cd, raw = make_corpus(tmp_path, shows)
    first = run_publish(tmp_path, FakeClient(), sp, cd, raw)

    reordered = list(json.loads(sp.read_text()))
    reordered.reverse()
    sp.write_text(json.dumps(reordered))
    (tmp_path / "manifest.json").unlink()
    second = run_publish(tmp_path, FakeClient(), sp, cd, raw)

    def anchor_ids(p):
        return sorted(f["frame_id"] for f in p["frames"] if f["role"] == "anchor")

    assert anchor_ids(first) == anchor_ids(second)


def test_eight_shows_give_two_of_them_a_second_anchor_and_none_a_third(tmp_path):
    shows = {f"show-{i:02d}": [f"org:o{i}", "class:dci-world"] for i in range(1, 9)}
    sp, cd, raw = make_corpus(tmp_path, shows)
    payload = run_publish(tmp_path, FakeClient(), sp, cd, raw)

    anchors = [f for f in payload["frames"] if f["role"] == "anchor"]
    assert len(anchors) == 10
    per_show: dict[str, int] = {}
    for f in anchors:
        per_show[f["show_id"]] = per_show.get(f["show_id"], 0) + 1
    assert sorted(per_show.values()) == [1] * 6 + [2, 2]
    assert max(per_show.values()) == 2


def test_a_single_curated_show_makes_all_ten_keepers_anchors(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, {"only-show": ["org:only"]})
    payload = run_publish(tmp_path, FakeClient(), sp, cd, raw)
    assert len(payload["frames"]) == 10
    assert all(f["role"] == "anchor" for f in payload["frames"])


def test_fewer_than_ten_keepers_makes_all_of_them_anchors_without_padding(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, {"only-show": ["org:only"]}, keepers=4)
    payload = run_publish(tmp_path, FakeClient(), sp, cd, raw)
    assert len(payload["frames"]) == 4
    assert sum(1 for f in payload["frames"] if f["role"] == "anchor") == 4


def test_rarity_puts_the_unusual_show_first():
    tags = {
        "common-a": ["era:2010s"],
        "common-b": ["era:2010s"],
        "rare": ["era:1970s"],
    }
    assert publish.order_shows(sorted(tags), tags)[0] == "rare"


def test_ties_break_on_show_id_ascending():
    tags = {"zulu": ["org:z"], "alpha": ["org:a"], "mike": ["org:m"]}
    assert publish.order_shows(["zulu", "mike", "alpha"], tags) == ["alpha", "mike", "zulu"]


# -------------------------------------------------------------- idempotence


def test_a_second_run_uploads_nothing(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, twelve_shows())
    client = FakeClient()
    run_publish(tmp_path, client, sp, cd, raw)
    first_uploads = len(client.files.uploaded)
    assert first_uploads == 12 * 11  # 10 frames + 1 shape per show

    run_publish(tmp_path, client, sp, cd, raw)
    assert len(client.files.uploaded) == first_uploads


def test_a_no_change_rerun_is_byte_identical_including_published_at(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, twelve_shows())
    client = FakeClient()
    run_publish(tmp_path, client, sp, cd, raw)
    before = (tmp_path / "manifest.json").read_bytes()

    run_publish(tmp_path, client, sp, cd, raw, run_date=date(2027, 1, 1))
    assert (tmp_path / "manifest.json").read_bytes() == before


def test_published_at_moves_when_the_corpus_does(tmp_path):
    shows = twelve_shows()
    sp, cd, raw = make_corpus(tmp_path, shows)
    client = FakeClient()
    run_publish(tmp_path, client, sp, cd, raw)

    (cd / "show-12.json").unlink()
    payload = run_publish(tmp_path, client, sp, cd, raw, run_date=date(2027, 1, 1))
    assert payload["published_at"] == "2027-01-01"


def test_a_dead_file_id_is_re_uploaded_on_the_next_run(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, {"only-show": ["org:only"]})
    client = FakeClient()
    payload = run_publish(tmp_path, client, sp, cd, raw)

    dead = payload["frames"][0]["file_id"]
    client.files.store.discard(dead)
    before = len(client.files.uploaded)

    again = run_publish(tmp_path, client, sp, cd, raw)
    assert len(client.files.uploaded) == before + 1
    assert again["frames"][0]["file_id"] != dead


# ------------------------------------------------------------------ failures


def test_an_upload_failure_partway_leaves_the_previous_manifest_intact(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, {"a-show": ["org:a"], "b-show": ["org:b"]})
    good = FakeClient()
    run_publish(tmp_path, good, sp, cd, raw)
    before = (tmp_path / "manifest.json").read_bytes()

    (cd / "c-show.json").write_text(json.dumps({
        "show_id": "c-show", "curated_at": "2026-08-25", "show_statement": "s",
        "shortlist": [8], "keepers": [{"rank": 1, "t": 8, "reason": "r"}],
    }))
    sources = json.loads(sp.read_text()) + [source_entry("c-show", ["org:c"])]
    sp.write_text(json.dumps(sources))
    frames = raw / "c-show" / "frames"
    frames.mkdir(parents=True)
    from PIL import Image
    Image.new("RGB", (16, 9)).save(frames / "t00008.jpg")
    Image.new("RGB", (16, 6)).save(raw / "c-show" / "shape.png")

    broken = FakeClient(fail_after=0)
    broken.files.store = set(good.files.store)
    with pytest.raises(RuntimeError):
        run_publish(tmp_path, broken, sp, cd, raw)
    assert (tmp_path / "manifest.json").read_bytes() == before


def test_no_manifest_is_written_when_a_file_id_does_not_resolve(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, {"a-show": ["org:a"]})

    class Amnesiac(FakeClient):
        """Uploads succeed but nothing is ever actually stored."""

        def __init__(self):
            super().__init__()
            real_upload = self.files.upload

            def upload(file, betas=None):
                result = real_upload(file, betas=betas)
                self.files.store.discard(result.id)
                return result

            self.files.upload = upload

    with pytest.raises(PublishError, match="do not resolve"):
        run_publish(tmp_path, Amnesiac(), sp, cd, raw)
    assert not (tmp_path / "manifest.json").exists()


def test_a_curated_show_missing_from_sources_is_named(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, {"a-show": ["org:a"]})
    (cd / "ghost.json").write_text(json.dumps({
        "show_id": "ghost", "keepers": [{"rank": 1, "t": 8, "reason": "r"}],
    }))
    with pytest.raises(PublishError, match="ghost"):
        run_publish(tmp_path, FakeClient(), sp, cd, raw)


def test_a_missing_frame_file_is_named(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, {"a-show": ["org:a"]})
    (raw / "a-show" / "frames" / "t00008.jpg").unlink()
    with pytest.raises(PublishError, match="a-show-t8"):
        run_publish(tmp_path, FakeClient(), sp, cd, raw)


def test_no_curations_at_all_is_a_clear_message(tmp_path):
    (tmp_path / "curation").mkdir()
    with pytest.raises(PublishError, match="story_014"):
        publish.load_curations(tmp_path / "curation")


# --------------------------------------------------------------- verify-only


def test_verify_only_passes_on_a_healthy_manifest(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, twelve_shows())
    client = FakeClient()
    run_publish(tmp_path, client, sp, cd, raw)

    client.files.list_calls = 0
    code = publish.verify_only(
        client,
        manifest_path=tmp_path / "manifest.json",
        status_path=tmp_path / "verify.json",
        run_date=RUN_DATE,
    )
    assert code == 0
    assert json.loads((tmp_path / "verify.json").read_text())["ok"] is True


def test_verify_only_makes_one_lookup_not_one_per_frame(tmp_path):
    sp, cd, raw = make_corpus(tmp_path, twelve_shows())
    client = FakeClient()
    run_publish(tmp_path, client, sp, cd, raw)

    client.files.list_calls = 0
    publish.verify_only(
        client,
        manifest_path=tmp_path / "manifest.json",
        status_path=tmp_path / "verify.json",
        run_date=RUN_DATE,
    )
    assert client.files.list_calls == 1  # not 132


def test_a_deleted_file_id_fails_verify_and_names_the_frame(tmp_path, capsys):
    sp, cd, raw = make_corpus(tmp_path, {"a-show": ["org:a"]})
    client = FakeClient()
    payload = run_publish(tmp_path, client, sp, cd, raw)
    victim = payload["frames"][3]
    client.files.store.discard(victim["file_id"])

    code = publish.verify_only(
        client,
        manifest_path=tmp_path / "manifest.json",
        status_path=tmp_path / "verify.json",
        run_date=RUN_DATE,
    )
    assert code == 1
    out = capsys.readouterr()
    assert victim["frame_id"] in out.out  # the Actions annotation
    assert "::warning" in out.out
    status = json.loads((tmp_path / "verify.json").read_text())
    assert status["corpus_verify_failed"] is True
    assert victim["frame_id"] in status["missing"]


def test_verify_only_with_no_manifest_reports_rather_than_crashing(tmp_path):
    code = publish.verify_only(
        FakeClient(),
        manifest_path=tmp_path / "nope.json",
        status_path=tmp_path / "verify.json",
        run_date=RUN_DATE,
    )
    assert code == 1
    assert json.loads((tmp_path / "verify.json").read_text())["ok"] is False


# -------------------------------------------------------------------- wiring


def test_the_workflow_verifies_the_corpus_without_ever_blocking_the_day():
    import re

    wf = (REPO_ROOT / ".github/workflows/daily-georgia.yml").read_text()
    assert "--verify-only" in wf
    step = wf[wf.index("--verify-only") - 700:wf.index("--verify-only") + 200]
    assert "continue-on-error: true" in step
    # and it runs before Georgia, so a warning lands on the same run
    assert wf.index("--verify-only") < wf.index("scripts.run_georgia")
