"""Tests for scripts/call_sonnet.py (mocked client)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.call_sonnet import SonnetOutputError, call_sonnet  # noqa: E402


def _mock_client(response_text: str) -> MagicMock:
    """Build a mock Anthropic client whose .messages.create returns a fake response."""
    response = SimpleNamespace(content=[SimpleNamespace(text=response_text)])
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def test_returns_tuple_when_both_tags_present():
    client = _mock_client("<site><html>hi</html></site>\n<log>---\ndate: x\n---\nbody</log>")
    html, diary = call_sonnet("prompt", client=client)
    assert html == "<html>hi</html>"
    assert diary.startswith("---")
    client.messages.create.assert_called_once()
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert call_kwargs["max_tokens"] == 16000
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
    client.messages.create.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        call_sonnet("prompt", client=client)


def test_handles_multiple_content_blocks():
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(content=[
        SimpleNamespace(text="<site>first "),
        SimpleNamespace(text="half</site><log>diary</log>"),
    ])
    html, diary = call_sonnet("prompt", client=client)
    assert html == "first half"
    assert diary == "diary"
