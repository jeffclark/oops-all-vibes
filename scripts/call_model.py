"""Thin wrapper around the Anthropic SDK for Georgia's daily call.

Takes an assembled prompt, calls the model, and returns the two pieces of
Georgia's output: today's HTML (inside <site>...</site>) and today's diary
entry (inside <log>...</log>). Missing or empty tags raise ModelOutputError.
API errors propagate — retry/fail-open logic lives in run_georgia.py.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from anthropic import Anthropic


MODEL = "claude-opus-5"

# History of this number, because it has bitten us twice:
#
# 8000 was enough when Georgia's HTML was ~12-18KB. Once we started asking her
# to also surface on-page reflection + yesterday's stats + Jeff's note, the
# response got truncated mid-HTML (no </site>, no <log>) — observed on
# 04-30, 05-02, and 05-03. 24000 was the floor that prevented truncation on
# Sonnet 4.6.
#
# 24000 is NOT safe on Opus 5, for two compounding reasons. Omitting the
# `thinking` parameter means adaptive thinking runs (on Sonnet 4.6 it meant no
# thinking at all), and thinking shares the max_tokens budget with the response
# text. On top of that the Opus tokenizer produces more tokens for the same
# text. A 5-day replay of real archived prompts (scripts/model_ab.py) measured
# Opus output between 18.9k and 30.7k tokens per day against Sonnet's ~11.7k
# average, so several days would have truncated at 24000.
#
# 64000 leaves roughly 2x headroom over the worst day observed. Revisit it as
# the archive grows — the prompt gains ~1.5KB/day, and output grows with it.
MAX_TOKENS = 64000

# Opus 5's safety classifiers can decline a request: HTTP 200 with
# stop_reason "refusal" rather than an error. Unhandled, that reaches the tag
# parser as an empty response, burns the retry, and costs a day of site.
# "default" lets the API re-run a declined request on its recommended
# fallback model inside the same call, routed by refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_SITE_RE = re.compile(r"<site>(.*?)</site>", re.DOTALL)
_LOG_RE = re.compile(r"<log>(.*?)</log>", re.DOTALL)


class ModelResult(NamedTuple):
    """Georgia's two outputs plus what the call cost in tokens.

    output_tokens includes adaptive thinking, which is billed but never
    returned, and it is what MAX_TOKENS caps — so it is the number to watch
    as the prompt grows.
    """

    html: str
    diary: str
    input_tokens: int
    output_tokens: int


class ModelOutputError(Exception):
    """Raised when the response lacks the required tags or either tag is empty."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


def call_model(prompt: str, client: Anthropic | None = None) -> ModelResult:
    """Call the model with `prompt`, return a ModelResult.

    Reads ANTHROPIC_API_KEY from env when `client` is not provided.
    Raises ModelOutputError if either <site> or <log> is missing or empty, or
    if the whole fallback chain declined the request.
    API errors (anthropic.APIError and subclasses) propagate.
    """
    if client is None:
        client = Anthropic()
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()

    # Text blocks only. Thinking blocks are billed but come back with empty
    # text, and must not reach the tag parser.
    raw = "".join(block.text for block in message.content if block.type == "text")

    if message.stop_reason == "refusal":
        detail = getattr(message.stop_details, "category", None)
        raise ModelOutputError(
            f"Request was declined by safety classifiers (category: {detail}). "
            "The fallback chain did not recover it.",
            raw=raw,
        )

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
        truncated = " (response hit max_tokens)" if message.stop_reason == "max_tokens" else ""
        raise ModelOutputError(
            f"Model output missing or empty: {', '.join(missing)}{truncated}",
            raw=raw,
        )

    return ModelResult(
        html=site_text,
        diary=log_text,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
    )
