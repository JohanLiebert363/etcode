"""Talks to the AI providers through LiteLLM."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from . import config

MAX_ATTEMPTS = 4
MAX_WAIT = 90          # never wait longer than this between retries (seconds)
EFFORT_PROVIDERS = {"openai", "gemini"}   # Claude needs thinking blocks passed back with tools; not supported yet
_last_request = [0.0]
_learned: dict[str, int] = {}   # per-minute limits discovered from errors, per provider


class MissingKeyError(ValueError):
    pass


@dataclass
class Reply:
    """One answer from the AI. usage = {prompt, completion, estimated, cost} (cost may be None)."""
    content: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict | None = None


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


def describe_rate_limit(error: Exception) -> str:
    """Which limit was hit, in plain words."""
    text = str(error).lower().replace(" ", "")
    if "perday" in text:
        return "its daily limit"
    if "insufficient_quota" in text or "limit:0" in text:
        return "its quota (the account may need billing turned on)"
    if "perminute" in text or suggested_wait(error) is not None:
        return "its per-minute limit"
    return "its request limit"


def learn_limit(provider: str, error: Exception):
    """If the error states a per-minute limit ("limit: 5"), pace requests below it from now on.
    Returns (limit, pace) when something new was learned. A pace set by the user always wins."""
    text = str(error)
    if "perminute" not in text.lower().replace(" ", "") or config.REQUESTS_PER_MINUTE:
        return None
    match = re.search(r'"quotaValue":\s*"(\d+)"', text) or re.search(r"limit:\s*(\d+)", text)
    if not match:
        return None
    limit = int(match.group(1))
    pace = max(1, limit - 1)
    if limit < 1 or _learned.get(provider) == pace:
        return None
    _learned[provider] = pace
    return limit, pace


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


def _pace(notify, provider: str | None = None) -> None:
    """Optional spacing between requests (ETCODE_REQUESTS_PER_MINUTE) for low-quota plans."""
    per_minute = config.REQUESTS_PER_MINUTE or _learned.get(provider, 0)
    if per_minute > 0:
        wait = _last_request[0] + 60.0 / per_minute - time.monotonic()
        if wait > 1 and notify:
            notify(f"Pacing requests to stay under {per_minute} per minute. Waiting {wait:.0f}s...")
        if wait > 0:
            time.sleep(wait)
    _last_request[0] = time.monotonic()


def _get(obj, name):
    if obj is None:
        return None
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _merge_tool_call(calls: list, piece) -> None:
    """Add one streamed tool-call fragment to the calls collected so far."""
    index, call_id, function = _get(piece, "index"), _get(piece, "id"), _get(piece, "function")
    name, arguments = _get(function, "name") or "", _get(function, "arguments") or ""
    target = None
    for existing in reversed(calls):
        same_index = index is None or existing["index"] == index
        same_id = not call_id or not existing["id"] or existing["id"] == call_id
        if same_index and same_id and (index is not None or not call_id):
            target = existing
            break
    if target is None:
        target = {"index": index if index is not None else len(calls), "id": "", "name": "", "args": ""}
        calls.append(target)
    if call_id and not target["id"]:
        target["id"] = call_id
    if name and not target["name"]:
        target["name"] = name
    elif name and name != target["name"]:
        target["name"] += name
    target["args"] += arguments


def collect_stream(chunks, on_text) -> Reply:
    """Read a streamed response, calling on_text for each piece of text as it arrives."""
    text, calls, usage = [], [], None
    for chunk in chunks:
        usage = _get(chunk, "usage") or usage
        choices = _get(chunk, "choices") or []
        delta = _get(choices[0], "delta") if choices else None
        if delta is None:
            continue
        piece = _get(delta, "content")
        if piece:
            text.append(piece)
            on_text(piece)
        for tool_piece in _get(delta, "tool_calls") or []:
            _merge_tool_call(calls, tool_piece)
    tool_calls = [SimpleNamespace(id=c["id"] or f"call_{i}", type="function",
                                  function=SimpleNamespace(name=c["name"], arguments=c["args"] or "{}"))
                  for i, c in enumerate(calls) if c["name"]]
    return Reply("".join(text), tool_calls, usage)


def _cost(model: str, prompt: int, completion: int):
    try:
        import litellm

        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model, prompt_tokens=prompt, completion_tokens=completion)
        return float(prompt_cost) + float(completion_cost)
    except Exception:
        return None   # a model litellm has no prices for


def finish_usage(reply: Reply, model: str, messages: list) -> Reply:
    """Turn the provider's usage numbers (or an estimate) into reply.usage."""
    raw = reply.usage
    prompt, completion = _get(raw, "prompt_tokens"), _get(raw, "completion_tokens")
    estimated = False
    if not prompt:
        prompt, estimated = estimate_tokens(messages), True
    if not completion:
        arguments = sum(len(c.function.arguments) for c in reply.tool_calls)
        completion, estimated = int((len(reply.content) + arguments) / 3.5), True
    reply.usage = {"prompt": int(prompt), "completion": int(completion), "estimated": estimated,
                   "cost": _cost(model, int(prompt), int(completion))}
    return reply


def chat(provider: str, messages: list, tools: list | None = None, notify=None, on_text=None,
         effort: str | None = None) -> Reply:
    """Send one request and return a Reply. With on_text, the answer is streamed:
    on_text(piece) is called as text arrives."""
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

    model = config.model_for(provider)
    kwargs = dict(
        model=model,
        api_key=key,
        messages=messages,
        timeout=config.REQUEST_TIMEOUT,
        num_retries=0,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if effort and provider in EFFORT_PROVIDERS:
        kwargs["reasoning_effort"] = effort
    if on_text is not None:
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

    for attempt in range(MAX_ATTEMPTS):
        _pace(notify, provider)
        shown = [False]

        def emit(piece, shown=shown):
            shown[0] = True
            on_text(piece)

        try:
            response = litellm.completion(**kwargs)
            if on_text is not None:
                reply = collect_stream(response, emit)
            else:
                message = response.choices[0].message
                reply = Reply(message.content or "", list(message.tool_calls or []), getattr(response, "usage", None))
            return finish_usage(reply, model, messages)
        except Exception as error:
            if shown[0]:
                raise    # text is already on screen; retrying would print it twice
            learned = learn_limit(provider, error) if is_rate_limit(error) else None
            if learned and notify:
                notify(f"This plan allows {learned[0]} requests per minute. "
                       f"Etcode will now pace itself to {learned[1]} per minute.")
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
