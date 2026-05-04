"""Thin wrapper around the Anthropic SDK for Georgia's daily call.

Takes an assembled prompt, calls Sonnet 4.6, and returns the two pieces of
Georgia's output: today's HTML (inside <site>...</site>) and today's diary
entry (inside <log>...</log>). Missing or empty tags raise SonnetOutputError.
API errors propagate — retry/fail-open logic lives in run_georgia.py.
"""
from __future__ import annotations

import re

from anthropic import Anthropic


MODEL = "claude-sonnet-4-6"
# 8000 was enough when Georgia's HTML was ~12-18KB. Once we started asking her
# to also surface on-page reflection + yesterday's stats + Jeff's note, the
# response got truncated mid-HTML (no </site>, no <log>) — observed on
# 04-30, 05-02, and 05-03. 24000 is the floor that prevents truncation.
# The SDK requires streaming for calls that may exceed 10 min wall time; at
# 24000 max_tokens on sonnet-4-6 that threshold is crossed, so we stream.
MAX_TOKENS = 24000

_SITE_RE = re.compile(r"<site>(.*?)</site>", re.DOTALL)
_LOG_RE = re.compile(r"<log>(.*?)</log>", re.DOTALL)


class SonnetOutputError(Exception):
    """Raised when Sonnet's response lacks the required tags or either tag is empty."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


def call_sonnet(prompt: str, client: Anthropic | None = None) -> tuple[str, str]:
    """Call Sonnet with `prompt`, return (html, diary).

    Reads ANTHROPIC_API_KEY from env when `client` is not provided.
    Raises SonnetOutputError if either <site> or <log> is missing or empty.
    API errors (anthropic.APIError and subclasses) propagate.
    """
    if client is None:
        client = Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        raw = stream.get_final_text()

    site_match = _SITE_RE.search(raw)
    log_match = _LOG_RE.search(raw)
    site_text = site_match.group(1).strip() if site_match else ""
    log_text = log_match.group(1).strip() if log_match else ""

    missing: list[str] = []
    if not site_text:
        missing.append("<site>...</site>")
    if not log_text:
        missing.append("<log>...</log>")
    if missing:
        raise SonnetOutputError(
            f"Sonnet output missing or empty: {', '.join(missing)}",
            raw=raw,
        )

    return site_text, log_text
