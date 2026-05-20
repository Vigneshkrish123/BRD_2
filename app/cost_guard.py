"""
Cost protection layer — three mechanisms:

1. Input token guard      Rejects transcripts that are too large
2. Rate limiter           Max N pipeline runs per hour (file-based, persists across sessions)
3. Daily circuit breaker  Tracks estimated USD spend; halts if daily budget exceeded

Azure-side hardening (do these in the Azure AI Foundry portal):
  - Set TPM quota on your gpt-4o-mini deployment (recommended: 40,000 TPM)
  - Set RPM quota (recommended: 10 RPM)
  - Create a Cost Management budget alert at your monthly limit
  These act as a second wall if this code is bypassed.
"""

import json
import time
from datetime import date
from pathlib import Path
from loguru import logger
import tiktoken


# ── Config — tune these to your needs ────────────────────────────────────────

MAX_INPUT_TOKENS      = 20_000   # ~15k words; hard reject above this
DAILY_BUDGET_USD      = 5.00     # circuit breaker — resets at midnight
MAX_REQUESTS_PER_HOUR = 10       # rate limit across all sessions

# gpt-4o-mini pricing (USD per token, as of 2025)
_PRICE_INPUT  = 0.15 / 1_000_000   # $0.15 per 1M input tokens
_PRICE_OUTPUT = 0.60 / 1_000_000   # $0.60 per 1M output tokens

_USAGE_FILE = Path(__file__).parent.parent / "outputs" / ".usage.json"
_ENCODER    = tiktoken.encoding_for_model("gpt-4o-mini")


# ── Token counting ────────────────────────────────────────────────────────────

def count_tokens(text: str) -> int:
    """Exact token count using the gpt-4o-mini tokeniser."""
    return len(_ENCODER.encode(text))


# ── Usage log ─────────────────────────────────────────────────────────────────

def _load_log() -> dict:
    if _USAGE_FILE.exists():
        try:
            return json.loads(_USAGE_FILE.read_text())
        except Exception:
            pass
    return {"date": str(date.today()), "requests": [], "total_usd": 0.0}


def _save_log(log: dict):
    _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _USAGE_FILE.write_text(json.dumps(log, indent=2))


def _today_log() -> dict:
    """Return today's log, resetting if the date has changed."""
    log = _load_log()
    if log.get("date") != str(date.today()):
        log = {"date": str(date.today()), "requests": [], "total_usd": 0.0}
        _save_log(log)
    return log


# ── Guards (call these before running the pipeline) ───────────────────────────

def check_input_size(text: str) -> int:
    """
    Raise ValueError if the transcript exceeds MAX_INPUT_TOKENS.
    Returns the token count so callers can display it.
    """
    tokens = count_tokens(text)
    if tokens > MAX_INPUT_TOKENS:
        raise ValueError(
            f"Transcript is {tokens:,} tokens — limit is {MAX_INPUT_TOKENS:,}. "
            "Trim the transcript (remove irrelevant sections) and retry."
        )
    logger.info(f"CostGuard | input ok: {tokens:,} tokens")
    return tokens


def check_rate_limit():
    """
    Raise RuntimeError if more than MAX_REQUESTS_PER_HOUR pipeline runs
    have occurred in the last 60 minutes.
    """
    log    = _today_log()
    now    = time.time()
    recent = [r for r in log["requests"] if now - r["ts"] < 3600]

    if len(recent) >= MAX_REQUESTS_PER_HOUR:
        oldest   = min(r["ts"] for r in recent)
        wait_sec = int(3600 - (now - oldest))
        raise RuntimeError(
            f"Rate limit: {MAX_REQUESTS_PER_HOUR} runs/hour reached. "
            f"Retry in {wait_sec // 60}m {wait_sec % 60}s."
        )
    logger.info(f"CostGuard | rate ok: {len(recent)}/{MAX_REQUESTS_PER_HOUR} runs this hour")


def check_daily_budget():
    """
    Raise RuntimeError if today's estimated spend has hit DAILY_BUDGET_USD.
    """
    log = _today_log()
    if log["total_usd"] >= DAILY_BUDGET_USD:
        raise RuntimeError(
            f"Daily budget of ${DAILY_BUDGET_USD:.2f} reached "
            f"(${log['total_usd']:.4f} spent today). Resets at midnight."
        )
    logger.info(f"CostGuard | budget ok: ${log['total_usd']:.4f} / ${DAILY_BUDGET_USD:.2f}")


# ── Usage recording (call this after each LLM call) ──────────────────────────

def record_usage(input_tokens: int, output_tokens: int) -> float:
    """
    Log a completed LLM call using actual token counts from the API response.
    Returns the cost of this call in USD.
    """
    cost = (input_tokens * _PRICE_INPUT) + (output_tokens * _PRICE_OUTPUT)
    log  = _today_log()

    log["requests"].append({
        "ts":           time.time(),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd":     round(cost, 6),
    })
    log["total_usd"] = round(log["total_usd"] + cost, 6)
    _save_log(log)

    logger.info(
        f"CostGuard | recorded ${cost:.6f} "
        f"(in={input_tokens}, out={output_tokens}) | "
        f"daily total: ${log['total_usd']:.4f}"
    )
    return cost


# ── Summary for UI display ────────────────────────────────────────────────────

def get_summary() -> dict:
    """Return today's usage stats for display in Streamlit."""
    log = _today_log()
    return {
        "date":            log["date"],
        "runs_today":      len(log["requests"]),
        "spend_today_usd": log["total_usd"],
        "budget_usd":      DAILY_BUDGET_USD,
        "remaining_usd":   round(DAILY_BUDGET_USD - log["total_usd"], 4),
        "runs_remaining":  MAX_REQUESTS_PER_HOUR,
    }
