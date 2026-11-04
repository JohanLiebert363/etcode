"""Talks to the AI providers through LiteLLM."""
from __future__ import annotations

import time

import config

MAX_ATTEMPTS = 4


class MissingKeyError(ValueError):
    pass


def _transient_types() -> tuple:
    import litellm

    names = ("RateLimitError", "ServiceUnavailableError", "Timeout", "APIConnectionError",
             "InternalServerError", "BadGatewayError")
    return tuple(t for t in (getattr(litellm, n, None) for n in names) if isinstance(t, type))


def _is_transient(error: Exception) -> bool:
    if isinstance(error, _transient_types()):
        return True
    text = str(error).lower()
    return any(marker in text for marker in ("503", "502", "504", "overloaded"))


def chat(provider: str, messages: list, tools: list | None = None, notify=None):
    """Send one request and return the assistant message (has .content and .tool_calls)."""
    if provider not in config.PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")

    key = config.get_key(provider)
    if not key:
        info = config.PROVIDERS[provider]
        raise MissingKeyError(
            f"No API key for {info['label']}. Add one on the setup page "
            f"(python api-get/app.py) or set {info['env']}."
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
        try:
            return litellm.completion(**kwargs).choices[0].message
        except Exception as error:
            if attempt < MAX_ATTEMPTS - 1 and _is_transient(error):
                wait = 2 ** (attempt + 1)
                if notify:
                    notify(f"The provider is busy. Retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise


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
