"""Talks to the AI providers through LiteLLM."""
from __future__ import annotations

import re
import time

from . import config

MAX_ATTEMPTS = 4
MAX_WAIT = 90          # never wait longer than this between retries (seconds)
_last_request = [0.0]


class MissingKeyError(ValueError):
    pass


def _transient_types() -> tuple:
    import litellm

    names = ("RateLimitError", "ServiceUnavailableError", "Timeout", "APIConnectionError",
             "InternalServerError", "BadGatewayError")
    return tuple(t for t in (getattr(litellm, n, None) for n in names) if isinstance(t, type))


def is_rate_limit(error: Exception) -> bool:
    text = str(error).lower()
    return (type(error).__name__ == "RateLimitError" or "429" in text
            or "resource_exhausted" in text or "rate limit" in text)


def suggested_wait(error: Exception) -> float | None:
    """Seconds the provider asked us to wait ("retry in 18.4s"), if it said."""
    text = str(error)
    for pattern in (r"retry in ([\d.]+)\s*s", r'"retryDelay":\s*"([\d.]+)s"', r"retry-after\D*(\d+)"):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def retry_delay(error: Exception, attempt: int) -> float | None:
    """How long to wait before trying again, or None when retrying cannot help."""
    text = str(error).lower()
    if is_rate_limit(error):
        # Daily limits and unpaid accounts will not clear by waiting a few seconds.
        if "perday" in text.replace(" ", "") or "insufficient_quota" in text or "limit: 0" in text:
            return None
        hint = suggested_wait(error)
        return min((hint + 1) if hint is not None else 10 * (attempt + 1), MAX_WAIT)
    if isinstance(error, _transient_types()) or any(m in text for m in ("503", "502", "504", "overloaded")):
        return 2 ** (attempt + 1)
    return None


def _pace(notify) -> None:
    """Optional spacing between requests (ETCODE_REQUESTS_PER_MINUTE) for low-quota plans."""
    per_minute = config.REQUESTS_PER_MINUTE
    if per_minute > 0:
        wait = _last_request[0] + 60.0 / per_minute - time.monotonic()
        if wait > 1 and notify:
            notify(f"Pacing requests to stay under {per_minute} per minute. Waiting {wait:.0f}s...")
        if wait > 0:
            time.sleep(wait)
    _last_request[0] = time.monotonic()


def chat(provider: str, messages: list, tools: list | None = None, notify=None):
    """Send one request and return the assistant message (has .content and .tool_calls)."""
    if provider not in config.PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")

    key = config.get_key(provider)
    if not key:
        info = config.PROVIDERS[provider]
        raise MissingKeyError(
            f"No API key for {info['label']}. Run `etcode keys` to add one, "
            f"or set {info['env']}."
        )

    import litellm  # imported late: it is slow to load

    litellm.drop_params = True
    litellm.suppress_debug_info = True

    kwargs = dict(
        model=config.model_for(provider),
        api_key=key,
        messages=messages,
        timeout=config.REQUEST_TIMEOUT,
        num_retries=0,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    for attempt in range(MAX_ATTEMPTS):
        _pace(notify)
        try:
            return litellm.completion(**kwargs).choices[0].message
        except Exception as error:
            wait = retry_delay(error, attempt)
            if wait is None or attempt == MAX_ATTEMPTS - 1:
                raise
            if notify:
                reason = "Rate limit reached." if is_rate_limit(error) else "The provider is busy."
                notify(f"{reason} Retrying in {wait:.0f}s...")
            time.sleep(wait)


def estimate_tokens(messages: list) -> int:
    """Rough size estimate (about 3.5 characters per token). Good enough for trimming."""
    chars = 0
    for m in messages:
        chars += len(str(m.get("content") or ""))
        chars += len(str(m.get("tool_calls") or ""))
    return int(chars / 3.5)


def trim_history(messages: list, budget_tokens: int) -> tuple[list, int]:
    """Drop the oldest whole turns until the history fits the budget.
    The newest turn is never dropped. Returns (messages, dropped_count)."""
    kept = list(messages)
    dropped = 0
    while estimate_tokens(kept) > budget_tokens:
        next_turn = next((i for i, m in enumerate(kept) if i > 0 and m.get("role") == "user"), None)
        if next_turn is None:
            break
        dropped += next_turn
        kept = kept[next_turn:]
    return kept, dropped
