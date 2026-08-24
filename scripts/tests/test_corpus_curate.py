"""Tests for scripts/corpus/curate.py — story_014.

No API calls: the client is faked at `beta.messages.stream`. Everything this
side of it is real — the refusal gates, what actually goes into the request,
count and timestamp validation, the retry, and the file that gets written.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.corpus import curate
from scripts.corpus.curate import CurationError

TIMES = list(range(0, 8 * 40, 8))          # 40 candidates
FIELD_TIMES = TIMES[:30]                    # 30 of them are field shots
OTHER_TIMES = TIMES[30:]


# ------------------------------------------------------------------ fixtures


def _jpg(path: Path, size=(64, 36)):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (30, 90, 40)).save(path)


def make_show(tmp_path: Path, show_id="bd-2014", field_times=None, sheets=True) -> Path:
    from PIL import Image, ImageDraw

    field_times = FIELD_TIMES if field_times is None else field_times
    show_dir = tmp_path / show_id
    for t in TIMES:
        _jpg(show_dir / "frames" / f"t{t:05d}.jpg")

    if sheets:
        _jpg(show_dir / "sheets" / "field_01.jpg", (256, 192))
        _jpg(show_dir / "sheets" / "field_02.jpg", (256, 192))
        _jpg(show_dir / "sheets" / "other_01.jpg", (256, 192))

    shape = Image.new("RGB", (1024, 384), (17, 17, 17))
    ImageDraw.Draw(shape).text((8, 8), "Blue Devils 2014", fill=(221, 221, 221))
    # something below the masked band, to prove the mask does not eat the plot
    ImageDraw.Draw(shape).rectangle((0, 200, 1023, 240), fill=(232, 232, 232))
    shape.save(show_dir / "shape.png")

    (show_dir / "ingest.json").write_text(json.dumps({
        "show_id": show_id,
        "frame_times": TIMES,
        "field_times": list(field_times),
        "field_frame_count": len(field_times),
    }))
    return show_dir


class _Stream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.beta = SimpleNamespace(messages=self)

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        assert self.replies, "the code made more calls than the test supplied replies"
        body = self.replies.pop(0)
        text = body if isinstance(body, str) else json.dumps(body)
        return _Stream(SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        ))


class ExplodingClient:
    """Any attempt to reach the API is a test failure."""

    def __getattr__(self, name):
        raise AssertionError(f"no API call should have been made (touched .{name})")


def shortlist_reply(times=None):
    return {"shortlist": list(times or FIELD_TIMES[:25])}


def keepers_reply(times=None, **over):
    times = list(times or FIELD_TIMES[:10])
    body = {
        "show_statement": "It keeps almost arriving and then declining to.",
        "keepers": [
            {"rank": i + 1, "t": t, "reason": f"verdict {i}"} for i, t in enumerate(times)
        ],
    }
    body.update(over)
    return body


def happy(tmp_path, show_id="bd-2014", **kw):
    return curate.curate_show(
        show_id,
        out_root=tmp_path,
        curation_dir=tmp_path / "curation",
        **kw,
    )


# ------------------------------------------------------------- refusal gates


def test_missing_sheets_dir_refuses_before_any_call(tmp_path):
    make_show(tmp_path, sheets=False)
    with pytest.raises(CurationError, match="no sheets/ directory"):
        happy(tmp_path, client=ExplodingClient())


def test_a_show_with_no_field_frames_points_back_at_story_013(tmp_path):
    make_show(tmp_path, field_times=[])
    with pytest.raises(CurationError, match="Fix the show at story_013"):
        happy(tmp_path, client=ExplodingClient())


def test_a_show_with_no_field_frames_never_falls_back_to_other(tmp_path):
    make_show(tmp_path, field_times=[])
    with pytest.raises(CurationError) as exc:
        happy(tmp_path, client=ExplodingClient())
    assert "other_*" in str(exc.value)


def test_missing_ingest_json_refuses(tmp_path):
    show_dir = make_show(tmp_path)
    (show_dir / "ingest.json").unlink()
    with pytest.raises(CurationError, match="ingest"):
        happy(tmp_path, client=ExplodingClient())


def test_refuses_to_overwrite_without_force(tmp_path):
    make_show(tmp_path)
    (tmp_path / "curation").mkdir()
    (tmp_path / "curation" / "bd-2014.json").write_text("{}")
    with pytest.raises(CurationError, match="--force"):
        happy(tmp_path, client=ExplodingClient())


def test_force_allows_replacing_an_existing_curation(tmp_path):
    make_show(tmp_path)
    (tmp_path / "curation").mkdir()
    (tmp_path / "curation" / "bd-2014.json").write_text("{}")
    client = FakeClient([shortlist_reply(), keepers_reply()])
    result = happy(tmp_path, client=client, force=True)
    assert len(result.keepers) == 10


def test_cli_without_show_explains_that_curation_is_per_show(tmp_path, capsys):
    assert curate.main([]) == 2
    assert "per-show" in capsys.readouterr().err


# ------------------------------------------------------- what reaches the model


def test_round_1_sends_only_field_sheets(tmp_path):
    make_show(tmp_path)
    client = FakeClient([shortlist_reply(), keepers_reply()])
    happy(tmp_path, client=client)

    round_1 = client.calls[0]["messages"][0]["content"]
    images = [b for b in round_1 if b["type"] == "image"]
    # two field sheets plus the shape plot; the other_* sheet is not sent
    assert len(images) == 3
    labels = " ".join(b["text"] for b in round_1 if b["type"] == "text")
    assert "other" not in labels.lower()


def test_round_1_never_names_the_corps_or_the_year(tmp_path):
    make_show(tmp_path)
    client = FakeClient([shortlist_reply(), keepers_reply()])
    happy(tmp_path, client=client)
    text = " ".join(
        b["text"] for b in client.calls[0]["messages"][0]["content"] if b["type"] == "text"
    )
    assert "Blue Devils" not in text
    assert "2014" not in text
    assert "bd-2014" not in text


def test_the_shape_plot_caption_is_masked_out(tmp_path):
    """ingest burns "<corps> <year>" into shape.png; round 1 must not carry it."""
    from PIL import Image
    import io

    show_dir = make_show(tmp_path)
    masked = Image.open(io.BytesIO(curate.anonymised_shape(show_dir / "shape.png")))
    assert masked.size == (1024, 384)
    # the caption band is uniformly background...
    band = masked.crop(curate.SHAPE_TITLE_BOX).getcolors()
    assert band == [(560 * 30, curate.SHAPE_BG)]
    # ...and the trace below it survives
    assert masked.getpixel((500, 220)) == (232, 232, 232)


def test_round_2_sends_each_shortlisted_frame_individually(tmp_path):
    make_show(tmp_path)
    client = FakeClient([shortlist_reply(), keepers_reply()])
    happy(tmp_path, client=client)

    round_2 = client.calls[1]["messages"][0]["content"]
    images = [b for b in round_2 if b["type"] == "image"]
    assert len(images) == curate.SHORTLIST_N + 1  # 25 frames plus the shape plot
    labels = [b["text"] for b in round_2 if b["type"] == "text"]
    assert labels[:3] == ["t=0", "t=8", "t=16"]


def test_the_soul_doc_is_the_system_prompt_and_the_diary_is_not_sent(tmp_path):
    make_show(tmp_path)
    client = FakeClient([shortlist_reply(), keepers_reply()])
    happy(tmp_path, client=client)
    system = client.calls[0]["system"]
    assert "My name is Georgia" in system
    assert "importance:" not in system  # no diary entries


def test_both_rounds_use_a_structured_output_schema(tmp_path):
    """Shape is enforced by the API; the counts cannot be, so they are enforced here.

    Constrained decoding rejects `minItems` above 1 and rejects `maxItems`
    outright, on both output_config.format and strict tool use. What the schema
    still buys is a guaranteed object of the right shape with no extra keys.
    """
    make_show(tmp_path)
    client = FakeClient([shortlist_reply(), keepers_reply()])
    happy(tmp_path, client=client)
    for call, key in zip(client.calls, ("shortlist", "keepers")):
        schema = call["output_config"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert key in schema["required"]
        array = schema["properties"][key]
        assert array["type"] == "array"
        assert array.get("minItems", 0) <= 1, "the API rejects minItems above 1"
        assert "maxItems" not in array, "the API rejects maxItems"


# -------------------------------------------------------------- round 1 rules


def test_round_1_must_return_exactly_25(tmp_path):
    make_show(tmp_path)
    short = shortlist_reply(FIELD_TIMES[:24])
    client = FakeClient([short, shortlist_reply(), keepers_reply()])
    result = happy(tmp_path, client=client)
    assert len(result.shortlist) == 25
    assert len(client.calls) == 3  # round 1, its retry, round 2


def test_a_short_round_1_retry_shows_the_model_its_own_answer(tmp_path):
    make_show(tmp_path)
    client = FakeClient([shortlist_reply(FIELD_TIMES[:24]), shortlist_reply(), keepers_reply()])
    happy(tmp_path, client=client)

    retry_messages = client.calls[1]["messages"]
    assert [m["role"] for m in retry_messages] == ["user", "assistant", "user"]
    assert "24 timestamps" in retry_messages[2]["content"][0]["text"]


def test_round_1_rejects_a_long_list(tmp_path):
    make_show(tmp_path)
    long_list = shortlist_reply(FIELD_TIMES[:26])
    client = FakeClient([long_list, long_list])
    with pytest.raises(CurationError, match="need exactly 25"):
        happy(tmp_path, client=client)


def test_round_1_rejects_duplicates(tmp_path):
    make_show(tmp_path)
    dupes = shortlist_reply(FIELD_TIMES[:24] + [FIELD_TIMES[0]])
    client = FakeClient([dupes, dupes])
    with pytest.raises(CurationError, match="duplicate"):
        happy(tmp_path, client=client)


def test_a_fabricated_timestamp_fails_the_show(tmp_path):
    make_show(tmp_path)
    fake = shortlist_reply(FIELD_TIMES[:24] + [99999])
    client = FakeClient([fake, fake])
    with pytest.raises(CurationError, match="not on any sheet"):
        happy(tmp_path, client=client)


def test_a_timestamp_she_was_not_shown_is_rejected_even_though_it_exists(tmp_path):
    """OTHER_TIMES are real candidates, but they were not on the sheets she saw."""
    make_show(tmp_path)
    sneaky = shortlist_reply(FIELD_TIMES[:24] + [OTHER_TIMES[0]])
    client = FakeClient([sneaky, sneaky])
    with pytest.raises(CurationError, match="not on any sheet"):
        happy(tmp_path, client=client)


# -------------------------------------------------------------- round 2 rules


def test_round_2_must_return_exactly_10(tmp_path):
    make_show(tmp_path)
    nine = keepers_reply(FIELD_TIMES[:9])
    client = FakeClient([shortlist_reply(), nine, nine])
    with pytest.raises(CurationError, match="need exactly 10"):
        happy(tmp_path, client=client)


def test_round_2_ranks_must_be_1_to_10_exactly_once(tmp_path):
    make_show(tmp_path)
    body = keepers_reply()
    body["keepers"][3]["rank"] = 1
    client = FakeClient([shortlist_reply(), body, body])
    with pytest.raises(CurationError, match="ranks must be"):
        happy(tmp_path, client=client)


def test_round_2_rejects_an_empty_reason(tmp_path):
    make_show(tmp_path)
    body = keepers_reply()
    body["keepers"][2]["reason"] = "   "
    client = FakeClient([shortlist_reply(), body, body])
    with pytest.raises(CurationError, match="empty reason"):
        happy(tmp_path, client=client)


def test_round_2_rejects_an_empty_show_statement(tmp_path):
    make_show(tmp_path)
    body = keepers_reply(show_statement="")
    client = FakeClient([shortlist_reply(), body, body])
    with pytest.raises(CurationError, match="show_statement"):
        happy(tmp_path, client=client)


def test_round_2_cannot_keep_a_frame_that_was_not_shortlisted(tmp_path):
    make_show(tmp_path)
    body = keepers_reply(FIELD_TIMES[:9] + [FIELD_TIMES[27]])
    client = FakeClient([shortlist_reply(), body, body])
    with pytest.raises(CurationError, match="not in the 25"):
        happy(tmp_path, client=client)


def test_round_2_recovers_on_the_retry(tmp_path):
    make_show(tmp_path)
    client = FakeClient([shortlist_reply(), keepers_reply(FIELD_TIMES[:9]), keepers_reply()])
    result = happy(tmp_path, client=client)
    assert [k.rank for k in result.keepers] == list(range(1, 11))


# -------------------------------------------------------------------- output


def test_writes_a_curation_file_matching_the_documented_shape(tmp_path):
    make_show(tmp_path)
    client = FakeClient([shortlist_reply(), keepers_reply()])
    happy(tmp_path, client=client)

    written = json.loads((tmp_path / "curation" / "bd-2014.json").read_text())
    assert set(written) == {"show_id", "curated_at", "show_statement", "shortlist", "keepers"}
    assert written["show_id"] == "bd-2014"
    assert len(written["shortlist"]) == 25
    assert [k["rank"] for k in written["keepers"]] == list(range(1, 11))
    assert all(k["reason"] for k in written["keepers"])
    assert all(t in TIMES for t in written["shortlist"])


def test_keepers_are_written_in_rank_order_whatever_order_she_used(tmp_path):
    make_show(tmp_path)
    body = keepers_reply()
    body["keepers"].reverse()
    client = FakeClient([shortlist_reply(), body])
    result = happy(tmp_path, client=client)
    assert [k.rank for k in result.keepers] == list(range(1, 11))


def test_a_failed_round_leaves_no_curation_file_behind(tmp_path):
    make_show(tmp_path)
    fake = shortlist_reply(FIELD_TIMES[:24] + [99999])
    client = FakeClient([fake, fake])
    with pytest.raises(CurationError):
        happy(tmp_path, client=client)
    assert not (tmp_path / "curation" / "bd-2014.json").exists()


def test_round_2_asks_her_what_the_cap_took():
    assert "sorry to lose" in curate.ROUND_2_TASK
