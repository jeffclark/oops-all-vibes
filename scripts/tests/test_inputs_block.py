"""Tests for the Layer 5 inputs block in scripts/assemble_prompt.py."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import scripts.assemble_prompt as ap  # noqa: E402


RUN_DATE = date(2026, 8, 21)


def _payload(**overrides) -> dict:
    payload = {
        "date": RUN_DATE.isoformat(),
        "inputs": {
            "fsa": {"label": "photo", "data": {
                "title": "Conversion. Flooring to gunstocks.",
                "date": "1942-01-01",
                "location": ["louisville", "kentucky"],
                "image": "https://tile.loc.gov/x.jpg#h=810&w=1024",
                "era": "OWI (wartime)",
                "is_oklahoma": False,
                "collection_total": 171074,
            }},
            "civic": {"label": "311", "data": {
                "day": "2026-08-19", "total_cases": 462, "distinct_types": 28,
                "top": [{"case": "Parking Enforcement", "n": 261}],
                "only_one_of": ["Overcrowding"],
                "resolved": [{"case": "Needle Pickup", "neighborhood": "Dorchester",
                              "note": "Needle recovered. JT"}],
            }},
            "surplus": {"label": "surplus", "data": {
                "name": "Lateral File Cabinet", "seller": "City of Revere",
                "price": "5.00", "description": "Locking keys unavailable.",
                "url": "https://municibid.com/Listing/Details/1",
            }},
            "hockey": {"label": "hockey", "data": {
                "season": "2026-27 Men's Divisions", "record": "0-0", "games_played": 0,
                "last_result": None,
                "next_game": {"date": "2026-09-03", "opponent": "MD1 University of Oklahoma",
                              "home": True, "us": 0, "them": 0},
                "days_until_next_game": 13,
            }},
            "register": {"label": "register", "data": {
                "day": "2026-08-20", "published_that_day": 108, "type": "Notice",
                "agencies": ["Centers for Disease Control and Prevention"],
                "title": "Proposed Data Collection", "abstract": "The CDC invites comment.",
            }},
        },
        "failures": {},
        "rotation": {"builds_this_cycle": 1, "builds_until_retirement": 29, "retired": []},
    }
    payload.update(overrides)
    return payload


def test_narrative_names_every_source():
    out = ap.render_inputs_narrative(_payload())
    for expected in [
        "Conversion. Flooring to gunstocks.",
        "462 calls to 311",
        "Needle recovered. JT",
        "Lateral File Cabinet",
        "City of Revere",
        "MD1 University of Oklahoma",
        "Centers for Disease Control and Prevention",
    ]:
        assert expected in out, f"missing: {expected}"
    assert out.startswith("[inputs]") and out.endswith("[/inputs]")


def test_narrative_calls_out_an_oklahoma_photograph():
    p = _payload()
    p["inputs"]["fsa"]["data"]["is_oklahoma"] = True
    assert "from Oklahoma" in ap.render_inputs_narrative(p)


def test_narrative_counts_history_so_inputs_accumulate():
    history = [_payload() for _ in range(4)]
    history[0]["inputs"]["fsa"]["data"]["is_oklahoma"] = True
    out = ap.render_inputs_narrative(_payload(), history)
    assert "You have looked at 5 of these" in out
    assert "1 were from Oklahoma" in out


def test_narrative_reports_the_running_311_average():
    history = [_payload() for _ in range(2)]
    for h in history:
        h["inputs"]["civic"]["data"]["total_cases"] = 100
    out = ap.render_inputs_narrative(_payload(), history)
    assert "more than usual" in out
    assert "100" in out


def test_narrative_surfaces_failures_as_content():
    out = ap.render_inputs_narrative(_payload(failures={"hockey": "Timeout"}))
    assert "Didn't answer today: hockey" in out


def test_narrative_demands_a_retirement_when_the_cycle_is_up():
    p = _payload(rotation={"builds_this_cycle": 30, "builds_until_retirement": 0, "retired": []})
    out = ap.render_inputs_narrative(p)
    assert "retire one of these" in out
    assert "can't keep them all" in out


def test_narrative_survives_a_roster_of_one():
    p = _payload()
    p["inputs"] = {"hockey": p["inputs"]["hockey"]}
    out = ap.render_inputs_narrative(p)
    assert "Oklahoma State hockey" in out
    assert "311" not in out


def test_load_inputs_block_uses_sentinel_when_file_missing(tmp_path):
    assert ap.load_inputs_block(tmp_path, RUN_DATE) == ap.NO_INPUTS_SENTINEL


def test_load_inputs_block_uses_sentinel_when_every_source_failed(tmp_path):
    (tmp_path / f"{RUN_DATE.isoformat()}.json").write_text(
        json.dumps({"inputs": {}, "failures": {"fsa": "boom"}})
    )
    assert ap.load_inputs_block(tmp_path, RUN_DATE) == ap.NO_INPUTS_SENTINEL


def test_load_inputs_block_uses_sentinel_on_corrupt_json(tmp_path):
    (tmp_path / f"{RUN_DATE.isoformat()}.json").write_text("{{{not json")
    assert ap.load_inputs_block(tmp_path, RUN_DATE) == ap.NO_INPUTS_SENTINEL


def test_load_inputs_block_renders_when_present(tmp_path):
    (tmp_path / f"{RUN_DATE.isoformat()}.json").write_text(json.dumps(_payload()))
    out = ap.load_inputs_block(tmp_path, RUN_DATE)
    assert "Lateral File Cabinet" in out


def test_inputs_block_reaches_the_assembled_prompt(tmp_path, monkeypatch):
    """The block is worthless if it never lands in the prompt."""
    for name in ("log", "feedback", "archive", "inputs"):
        (tmp_path / name).mkdir()
    (tmp_path / "georgia-soul.md").write_text("# soul")
    (tmp_path / "facts.json").write_text(json.dumps({"name": "Jeff Clark", "projects": []}))
    (tmp_path / "inputs" / f"{RUN_DATE.isoformat()}.json").write_text(json.dumps(_payload()))

    prompt = ap.assemble_prompt(RUN_DATE, repo_root=tmp_path)
    assert "[inputs]" in prompt
    assert "Lateral File Cabinet" in prompt
    assert "City of Revere" in prompt


def test_a_tied_game_is_not_reported_as_a_loss():
    """The fetcher counts ties, so the renderer has to handle them."""
    p = _payload()
    p["inputs"]["hockey"]["data"]["last_result"] = {
        "date": "2026-11-08", "opponent": "MD2 University of Arkansas",
        "home": True, "us": 3, "them": 3,
    }
    out = ap.render_inputs_narrative(p)
    assert "tied MD2 University of Arkansas 3-3" in out
    assert "lost to" not in out


def test_a_win_and_a_loss_still_read_correctly():
    for us, them, verb in ((5, 2, "beat"), (1, 4, "lost to")):
        p = _payload()
        p["inputs"]["hockey"]["data"]["last_result"] = {
            "date": "2026-11-08", "opponent": "Arkansas", "home": True,
            "us": us, "them": them,
        }
        assert f"they {verb} Arkansas" in ap.render_inputs_narrative(p)
