"""Tests for scripts/call_model.py (mocked client)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.call_model import (  # noqa: E402
    FALLBACK_BETA,
    MAX_TOKENS,
    MODEL,
    ModelOutputError,
    call_model,
)


def _block(kind: str, text: str = "") -> MagicMock:
    block = MagicMock()
    block.type = kind
    block.text = text
    return block


def _mock_client(
    response_text: str,
    *,
    stop_reason: str = "end_turn",
    blocks: list[MagicMock] | None = None,
    category: str | None = None,
    input_tokens: int = 60_000,
    output_tokens: int = 14_000,
) -> MagicMock:
    """Mock whose beta.messages.stream yields a final message with text blocks."""
    message = MagicMock()
    message.content = blocks if blocks is not None else [_block("text", response_text)]
    message.stop_reason = stop_reason
    message.stop_details.category = category
    message.usage.input_tokens = input_tokens
    message.usage.output_tokens = output_tokens
    client = MagicMock()
    client.beta.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = message
    return client


def test_returns_tuple_when_both_tags_present():
    client = _mock_client("<site><html>hi</html></site>\n<log>---\ndate: x\n---\nbody</log>")
    result = call_model("prompt", client=client)
    assert result.html == "<html>hi</html>"
    assert result.diary.startswith("---")


def test_sends_opus_5_config_with_fallbacks():
    client = _mock_client("<site>html</site><log>diary</log>")
    call_model("prompt", client=client)
    kwargs = client.beta.messages.stream.call_args.kwargs
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["max_tokens"] == 64000
    assert kwargs["betas"] == [FALLBACK_BETA]
    assert kwargs["fallbacks"] == "default"
    assert kwargs["messages"] == [{"role": "user", "content": "prompt"}]
    # `thinking` is deliberately omitted — Opus 5 runs adaptive thinking by default.
    assert "thinking" not in kwargs


def test_constants_match_what_is_sent():
    assert MODEL == "claude-opus-5"
    assert MAX_TOKENS == 64000


def test_uses_the_beta_endpoint_not_the_plain_one():
    """fallbacks is a beta parameter; the plain endpoint would reject it."""
    client = _mock_client("<site>html</site><log>diary</log>")
    call_model("prompt", client=client)
    client.beta.messages.stream.assert_called_once()
    client.messages.stream.assert_not_called()


def test_thinking_blocks_never_reach_the_tag_parser():
    """Opus 5 returns thinking blocks with empty text; only text blocks count."""
    blocks = [
        _block("thinking", ""),
        _block("text", "<site><html>hi</html></site><log>diary</log>"),
    ]
    result = call_model("prompt", client=_mock_client("", blocks=blocks))
    assert result.html == "<html>hi</html>"
    assert result.diary == "diary"


def test_refusal_raises_rather_than_parsing_empty_output():
    client = _mock_client("", stop_reason="refusal", category="cyber")
    with pytest.raises(ModelOutputError) as exc:
        call_model("prompt", client=client)
    assert "declined" in str(exc.value)
    assert "cyber" in str(exc.value)


def test_truncation_is_named_in_the_error():
    client = _mock_client("<site>cut off mid-", stop_reason="max_tokens")
    with pytest.raises(ModelOutputError) as exc:
        call_model("prompt", client=client)
    assert "max_tokens" in str(exc.value)


def test_raises_when_site_tag_missing():
    client = _mock_client("<log>diary</log>")
    with pytest.raises(ModelOutputError) as exc:
        call_model("prompt", client=client)
    assert "<site>" in str(exc.value)
    assert exc.value.raw == "<log>diary</log>"


def test_raises_when_log_tag_missing():
    client = _mock_client("<site>html</site>")
    with pytest.raises(ModelOutputError) as exc:
        call_model("prompt", client=client)
    assert "<log>" in str(exc.value)


def test_raises_when_both_tags_missing():
    client = _mock_client("just a bare response with no tags")
    with pytest.raises(ModelOutputError) as exc:
        call_model("prompt", client=client)
    assert "<site>" in str(exc.value)
    assert "<log>" in str(exc.value)


def test_raises_when_site_tag_empty():
    client = _mock_client("<site>   </site><log>diary</log>")
    with pytest.raises(ModelOutputError):
        call_model("prompt", client=client)


def test_raises_when_log_tag_empty():
    client = _mock_client("<site>html</site><log></log>")
    with pytest.raises(ModelOutputError):
        call_model("prompt", client=client)


def test_api_errors_propagate():
    client = MagicMock()
    client.beta.messages.stream.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        call_model("prompt", client=client)


def test_mid_stream_error_propagates():
    client = MagicMock()
    client.beta.messages.stream.return_value.__enter__.return_value.get_final_message.side_effect = RuntimeError("mid-stream boom")
    with pytest.raises(RuntimeError, match="mid-stream boom"):
        call_model("prompt", client=client)


def test_returns_the_token_counts_from_usage():
    client = _mock_client("<site>html</site><log>diary</log>")
    result = call_model("prompt", client=client)
    assert result.input_tokens == 60_000
    assert result.output_tokens == 14_000
