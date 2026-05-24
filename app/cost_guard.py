"""
Input protection — single mechanism:

Rejects transcripts that exceed MAX_INPUT_TOKENS before any LLM call.
Rate limiting and budget tracking removed — Azure AI Foundry TPM/RPM
quotas act as the cost protection layer instead.

Azure-side hardening (required — this is now your primary cost protection):
  - Set TPM quota on gpt-4o-mini deployment in AI Foundry portal (recommended: 40,000 TPM)
  - Set RPM quota (recommended: 10 RPM)
  - Create a Cost Management budget alert at your monthly limit
"""

from loguru import logger
import tiktoken

MAX_INPUT_TOKENS = 20_000  # ~15k words

_ENCODER = tiktoken.encoding_for_model("gpt-4o-mini")


def count_tokens(text: str) -> int:
    return len(_ENCODER.encode(text))


def check_input_size(text: str) -> int:
    """
    Raise ValueError if transcript exceeds MAX_INPUT_TOKENS.
    Returns token count so callers can display it.
    """
    tokens = count_tokens(text)
    if tokens > MAX_INPUT_TOKENS:
        raise ValueError(
            f"Transcript is {tokens:,} tokens — limit is {MAX_INPUT_TOKENS:,}. "
            "Trim the transcript and retry."
        )
    logger.info(f"CostGuard | input ok: {tokens:,} tokens")
    return tokens