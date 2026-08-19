"""Tests for scripts/model_ab.py (mocked client — no API calls)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.model_ab import (  # noqa: E402
    ARMS,
    Result,
    build_viewer,
    run_arm,
    select_dates,
)


SONNET, OPUS = ARMS


def _block(kind: str, text: str = "") -> MagicMock:
    block = MagicMock()
    block.type = kind
    block.text = text
    return block


def _mock_client(
    blocks: list[MagicMock],
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 60_000,
    output_tokens: int = 14_000,
) -> MagicMock:
    message = MagicMock()
    message.content = blocks
    message.stop_reason = stop_reason
    message.usage.input_tokens = input_tokens
    message.usage.output_tokens = output_tokens
    client = MagicMock()
    client.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = message
    return client


def _good_blocks() -> list[MagicMock]:
    return [_block("text", "<site><html>hi</html></site>\n<log>diary body</log>")]


def test_parses_both_tags_and_records_usage():
    client = _mock_client(_good_blocks())
    result, html, diary = run_arm(SONNET, "2026-08-19", "prompt", client)
    assert result.ok
    assert html == "<html>hi</html>"
    assert diary == "diary body"
    assert result.input_tokens == 60_000
    assert result.output_tokens == 14_000


def test_arms_send_their_own_model_and_max_tokens():
    for arm in ARMS:
        client = _mock_client(_good_blocks())
        run_arm(arm, "2026-08-19", "prompt", client)
        kwargs = client.messages.stream.call_args.kwargs
        assert kwargs["model"] == arm.model
        assert kwargs["max_tokens"] == arm.max_tokens
        assert kwargs["messages"] == [{"role": "user", "content": "prompt"}]
        # Neither arm sends `thinking` — that is what production does today,
        # and on Opus 5 the default is adaptive thinking.
        assert "thinking" not in kwargs


def test_sonnet_arm_matches_production_config():
    """If call_sonnet.py changes, this arm has drifted and the A/B is invalid."""
    from scripts.call_sonnet import MAX_TOKENS, MODEL

    assert SONNET.model == MODEL
    assert SONNET.max_tokens == MAX_TOKENS


def test_cost_uses_per_arm_rates():
    client = _mock_client(_good_blocks(), input_tokens=1_000_000, output_tokens=1_000_000)
    sonnet_result, _, _ = run_arm(SONNET, "2026-08-19", "prompt", client)
    assert sonnet_result.cost_usd == pytest.approx(18.0)  # $3 in + $15 out

    client = _mock_client(_good_blocks(), input_tokens=1_000_000, output_tokens=1_000_000)
    opus_result, _, _ = run_arm(OPUS, "2026-08-19", "prompt", client)
    assert opus_result.cost_usd == pytest.approx(30.0)  # $5 in + $25 out


def test_thinking_blocks_are_excluded_from_parsed_text():
    blocks = [
        _block("thinking", ""),
        _block("text", "<site><html>hi</html></site><log>d</log>"),
    ]
    result, html, _ = run_arm(OPUS, "2026-08-19", "prompt", client=_mock_client(blocks))
    assert result.ok
    assert html == "<html>hi</html>"


def test_truncation_is_reported_with_stop_reason():
    blocks = [_block("text", "<site><html>cut off mid")]
    result, _, _ = run_arm(OPUS, "2026-08-19", "prompt", client=_mock_client(blocks, stop_reason="max_tokens"))
    assert not result.ok
    assert "max_tokens" in result.error
    assert result.stop_reason == "max_tokens"


def test_refusal_is_reported_not_parsed():
    blocks = [_block("text", "")]
    result, html, _ = run_arm(OPUS, "2026-08-19", "prompt", client=_mock_client(blocks, stop_reason="refusal"))
    assert not result.ok
    assert "refusal" in result.error
    assert html == ""


def test_api_error_is_captured_not_raised():
    client = MagicMock()
    client.messages.stream.side_effect = RuntimeError("boom")
    result, html, diary = run_arm(OPUS, "2026-08-19", "prompt", client)
    assert not result.ok
    assert "RuntimeError: boom" in result.error
    assert (html, diary) == ("", "")


def test_select_dates_takes_the_most_recent(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for day in ("2026-08-01", "2026-08-02", "2026-08-03"):
        (prompts / f"{day}.md").write_text("x")
    assert select_dates(tmp_path, 2, None) == ["2026-08-02", "2026-08-03"]


def test_select_dates_honours_explicit_list(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "2026-08-01.md").write_text("x")
    assert select_dates(tmp_path, 5, "2026-08-01") == ["2026-08-01"]
    with pytest.raises(SystemExit):
        select_dates(tmp_path, 5, "2026-01-01")


def test_viewer_renders_both_outcomes(tmp_path):
    (tmp_path / "2026-08-19").mkdir(parents=True)
    (tmp_path / "2026-08-19" / "sonnet.html").write_text("<p>site</p>")
    results = [
        Result(date="2026-08-19", arm="sonnet", model=SONNET.model, ok=True, cost_usd=0.31),
        Result(date="2026-08-19", arm="opus", model=OPUS.model, ok=False, error="missing <site>"),
    ]
    build_viewer(results, ["2026-08-19"], tmp_path)
    html = (tmp_path / "index.html").read_text()
    assert 'src="2026-08-19/sonnet.html"' in html
    assert "missing &lt;site&gt;" in html  # escaped, not injected


# ---------- validation gate ----------

REAL_HTML = (REPO_ROOT / "archive" / "2026-08-18.html").read_text()
REAL_DIARY = (REPO_ROOT / "log" / "2026-08-18.md").read_text()
REAL_FACTS = __import__("json").loads((REPO_ROOT / "facts.json").read_text())


def test_real_shipped_output_passes_the_gate():
    """A page that actually shipped must validate, or the gate is wired up wrong."""
    client = _mock_client([_block("text", f"<site>{REAL_HTML}</site><log>{REAL_DIARY}</log>")])
    result, _, _ = run_arm(SONNET, "2026-08-18", "prompt", client, REAL_FACTS)
    assert result.ok
    assert result.valid, result.validation_failures
    assert result.validation_failures == []


def test_parsed_but_undersized_page_is_flagged_invalid():
    client = _mock_client(_good_blocks())
    result, html, _ = run_arm(SONNET, "2026-08-18", "prompt", client, REAL_FACTS)
    assert result.ok  # tags parsed fine
    assert not result.valid  # but it would have triggered a retry
    assert any("too small" in f for f in result.validation_failures)


def test_missing_inviolable_fact_is_flagged():
    stripped = REAL_HTML.replace(REAL_FACTS["email"], "redacted@example.com")
    client = _mock_client([_block("text", f"<site>{stripped}</site><log>{REAL_DIARY}</log>")])
    result, _, _ = run_arm(SONNET, "2026-08-18", "prompt", client, REAL_FACTS)
    assert result.ok
    assert not result.valid
    assert any("email" in f for f in result.validation_failures)


def test_viewer_shows_validation_outcome(tmp_path):
    (tmp_path / "2026-08-18").mkdir(parents=True)
    (tmp_path / "2026-08-18" / "sonnet.html").write_text("<p>site</p>")
    (tmp_path / "2026-08-18" / "opus.html").write_text("<p>site</p>")
    results = [
        Result(date="2026-08-18", arm="sonnet", model=SONNET.model, ok=True, valid=True),
        Result(
            date="2026-08-18", arm="opus", model=OPUS.model, ok=True, valid=False,
            validation_failures=["Your HTML is too small (12 bytes)."],
        ),
    ]
    build_viewer(results, ["2026-08-18"], tmp_path)
    html = (tmp_path / "index.html").read_text()
    assert "passes validation" in html
    assert "would have triggered a retry" in html
    assert "too small (12 bytes)" in html
