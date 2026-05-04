"""Tests for scripts/call_sonnet.py (mocked client)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.call_sonnet import SonnetOutputError, call_sonnet  # noqa: E402


def _mock_client(response_text: str) -> MagicMock:
    """Build a mock Anthropic client whose .messages.stream returns fake text."""
    client = MagicMock()
    client.messages.stream.return_value.__enter__.return_value.get_final_text.return_value = response_text
    return client


def test_returns_tuple_when_both_tags_present():
    client = _mock_client("<site><html>hi</html></site>\n<log>---\ndate: x\n---\nbody</log>")
    html, diary = call_sonnet("prompt", client=client)
    assert html == "<html>hi</html>"
    assert diary.startswith("---")
    client.messages.stream.assert_called_once()
    call_kwargs = client.messages.stream.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert call_kwargs["max_tokens"] == 24000
    assert call_kwargs["messages"] == [{"role": "user", "content": "prompt"}]


def test_raises_when_site_tag_missing():
    client = _mock_client("<log>diary</log>")
    with pytest.raises(SonnetOutputError) as exc:
        call_sonnet("prompt", client=client)
    assert "<site>" in str(exc.value)
    assert exc.value.raw == "<log>diary</log>"


def test_raises_when_log_tag_missing():
    client = _mock_client("<site>html</site>")
    with pytest.raises(SonnetOutputError) as exc:
        call_sonnet("prompt", client=client)
    assert "<log>" in str(exc.value)


def test_raises_when_both_tags_missing():
    client = _mock_client("just a bare response with no tags")
    with pytest.raises(SonnetOutputError) as exc:
        call_sonnet("prompt", client=client)
    assert "<site>" in str(exc.value)
    assert "<log>" in str(exc.value)


def test_raises_when_site_tag_empty():
    client = _mock_client("<site>   </site><log>diary</log>")
    with pytest.raises(SonnetOutputError):
        call_sonnet("prompt", client=client)


def test_raises_when_log_tag_empty():
    client = _mock_client("<site>html</site><log></log>")
    with pytest.raises(SonnetOutputError):
        call_sonnet("prompt", client=client)


def test_api_errors_propagate():
    client = MagicMock()
    client.messages.stream.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        call_sonnet("prompt", client=client)


def test_mid_stream_error_propagates():
    client = MagicMock()
    client.messages.stream.return_value.__enter__.return_value.get_final_text.side_effect = RuntimeError("mid-stream boom")
    with pytest.raises(RuntimeError, match="mid-stream boom"):
        call_sonnet("prompt", client=client)
