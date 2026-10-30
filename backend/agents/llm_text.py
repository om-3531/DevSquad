import logging
import re
import time
from typing import Any
from groq import RateLimitError

from backend.agents.model_manager import sanitize_secret

logger = logging.getLogger(__name__)


def strip_code_fences(text: str) -> str:
    content = (text or "").strip()
    if not content.startswith("```"):
        return content

    lines = content.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_retry_after(error: Exception) -> float | None:
    """Extract retry-after delay in seconds from HTTP headers or error message text."""
    response = getattr(error, "response", None)
    if response is not None:
        headers = getattr(response, "headers", {})
        for key in (
            "retry-after",
            "Retry-After",
            "x-ratelimit-reset-requests",
            "x-ratelimit-reset-tokens",
        ):
            val = headers.get(key)
            if val:
                try:
                    return max(float(val), 0.1)
                except (ValueError, TypeError):
                    pass

    # Search for regex patterns like "try again in 14.5s" or "try again in 14.5 seconds"
    err_str = str(error)
    patterns = [
        r"try again in (\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?",
        r"retry after (\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?",
        r"retry in (\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?",
        r"wait (\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, err_str, re.I)
        if match:
            try:
                return max(float(match.group(1)), 0.1)
            except (ValueError, TypeError):
                pass

    return None


def extract_model_from_error(error: Exception) -> str | None:
    """Extract model name from rate limit error message if present (e.g. 'model openai/gpt-oss-20b')."""
    err_str = str(error)
    match = re.search(r"model\s+['\"]?([A-Za-z0-9_./-]+)['\"]?", err_str, re.I)
    if match:
        return match.group(1).strip()
    return None


def is_rate_limit_error(error: Exception) -> bool:
    """Check if an exception represents a 429 rate limit from Groq or another provider."""
    if isinstance(error, RateLimitError):
        return True
    if getattr(error, "status_code", None) == 429:
        return True
    msg = str(error).lower()
    return "429" in msg or "rate limit" in msg or "ratelimit" in msg


def is_daily_limit_error(error: Exception) -> bool:
    """Check if the rate limit is a daily limit (Tokens Per Day / Requests Per Day)."""
    msg = str(error).lower()
    return "tpd" in msg or "day" in msg or "daily" in msg or "quota" in msg


def format_rate_limit_detail(
    error: Exception,
    model: str,
    retry_after: float | None = None,
    fallback_model: str | None = None,
) -> dict:
    """
    Construct a clear, structured rate-limit detail dictionary suitable
    for returning in HTTP 429 responses and displaying to the user.
    """
    clean_err = sanitize_secret(str(error))
    is_daily = is_daily_limit_error(error)

    if retry_after is not None:
        if retry_after >= 60:
            time_display = f"{int(retry_after // 60)}m {int(retry_after % 60)}s"
        else:
            time_display = f"{retry_after:.1f}s"
        retry_msg = f"Please retry in {time_display}."
    elif is_daily:
        retry_msg = "Daily token/request quota reached for this model."
    else:
        retry_msg = "Please wait a moment before retrying."

    fallback_msg = ""
    if fallback_model:
        fallback_msg = f" Available fallback model: '{fallback_model}' (set LLM_MODEL={fallback_model} in .env)."

    message = f"AI rate limit exceeded for model '{model}'. {retry_msg}{fallback_msg}"

    return {
        "detail": message,
        "model": model,
        "retry_after": retry_after,
        "is_daily_limit": is_daily,
        "fallback_model": fallback_model,
        "raw_error": clean_err[:300],
    }


def safe_log_completion(logger, model: str, finish_reason: str | None, content: str | None, usage=None) -> None:
    """Safely log model response metadata and token usage without exposing secrets."""
    content_len = len(content or "")
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
    total_tokens = getattr(usage, "total_tokens", None) if usage else None
    details = getattr(usage, "completion_tokens_details", None) if usage else None
    reasoning_tokens = getattr(details, "reasoning_tokens", None) if details else None

    logger.info(
        "Groq completion: model=%s finish_reason=%s content_length=%d prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s total_tokens=%s",
        model,
        finish_reason,
        content_len,
        prompt_tokens,
        completion_tokens,
        reasoning_tokens,
        total_tokens,
    )


def safe_log_rate_limit(logger, error: Exception, model: str, retry_after: float | None = None) -> None:
    """Log rate-limit events safely, stripping any tokens or keys."""
    sanitized_msg = sanitize_secret(str(error))
    logger.warning(
        "Rate limit hit on model=%s retry_after=%s error=%s",
        model,
        retry_after,
        sanitized_msg,
    )


def get_model_extra_kwargs(model: str) -> dict:
    """Return model-specific extra kwargs (e.g. reasoning_effort='low' for reasoning models)."""
    kwargs = {}
    lower = (model or "").lower()
    if any(k in lower for k in ("gpt-oss", "deepseek", "qwq", "r1", "reason")):
        kwargs["reasoning_effort"] = "low"
    return kwargs


def execute_chat_completion(
    client: Any,
    messages: list[dict],
    primary_model: str,
    fallback_model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    extra_kwargs: dict | None = None,
    agent_name: str = "Agent",
    max_retries_per_model: int = 2,
    base_backoff: float = 2.0,
    max_backoff: float = 10.0,
    on_model_switch: Any = None,
    logger: logging.Logger | None = None,
) -> tuple[Any, str]:
    """
    Execute chat completion with automatic rate-limit backoff and fallback model switching.

    1. Attempts completion with primary_model up to max_retries_per_model times.
    2. If primary_model encounters HTTP 429 (RateLimitError):
       - If daily limit or retry_after > max_backoff: immediately switches to fallback_model (if available).
       - If retry_after <= max_backoff: backs off with reasonable delay (retry_after + 0.5s) and retries.
       - If retries on primary_model exhaust: switches to fallback_model (if available).
    3. If fallback_model is switched to, on_model_switch(old_model, new_model, reason) is invoked.
    4. If fallback_model also exhausts retries, raises RateLimitError with sanitized details.
    """
    active_logger = logger or logging.getLogger(__name__)

    # Build sequence of models to attempt
    models_to_try = [primary_model]
    if fallback_model and fallback_model != primary_model:
        models_to_try.append(fallback_model)

    last_rate_limit_error: Exception | None = None
    last_error: Exception | None = None

    for model_index, model in enumerate(models_to_try):
        has_next_fallback = (model_index + 1 < len(models_to_try))
        next_fallback = models_to_try[model_index + 1] if has_next_fallback else None

        extra = extra_kwargs.copy() if extra_kwargs else {}
        extra.update(get_model_extra_kwargs(model))

        for attempt in range(max_retries_per_model):
            try:
                call_kwargs = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    **extra,
                }
                if max_tokens is not None:
                    call_kwargs["max_tokens"] = max_tokens

                response = client.chat.completions.create(**call_kwargs)
                choice = response.choices[0]
                content = getattr(choice.message, "content", "")
                finish_reason = getattr(choice, "finish_reason", None)
                safe_log_completion(active_logger, model, finish_reason, content, getattr(response, "usage", None))
                return response, model

            except Exception as error:
                last_error = error
                if is_rate_limit_error(error):
                    last_rate_limit_error = error
                    retry_after = extract_retry_after(error)
                    is_daily = is_daily_limit_error(error)
                    safe_log_rate_limit(active_logger, error, model, retry_after)

                    # Immediate fallback conditions:
                    # 1. Daily quota limit reached (TPD / RPD)
                    # 2. Retry delay exceeds max allowed backoff (> max_backoff)
                    if (is_daily or (retry_after and retry_after > max_backoff)) and has_next_fallback:
                        reason = "daily_quota_exhausted" if is_daily else f"retry_delay_{retry_after:.1f}s_exceeds_max_backoff"
                        active_logger.warning(
                            "%s: Rate limit on model '%s' (%s). Immediately switching to fallback model '%s'.",
                            agent_name, model, reason, next_fallback,
                        )
                        if on_model_switch:
                            try:
                                on_model_switch(model, next_fallback, reason)
                            except Exception as cb_err:
                                active_logger.warning("on_model_switch callback error: %s", cb_err)
                        break

                    # Reasonable retry backoff if attempts remain for this model:
                    if attempt < max_retries_per_model - 1:
                        if retry_after is not None:
                            delay = min(max(retry_after + 0.5, 1.0), max_backoff)
                        else:
                            delay = min(base_backoff * (2 ** attempt), max_backoff)

                        active_logger.warning(
                            "%s: Rate limit on '%s' (attempt %d/%d). Backing off for %.1fs before retrying.",
                            agent_name, model, attempt + 1, max_retries_per_model, delay,
                        )
                        time.sleep(delay)
                        continue

                    # Model retries exhausted:
                    if has_next_fallback:
                        reason = "model_retries_exhausted"
                        active_logger.warning(
                            "%s: Rate limit retries exhausted on model '%s'. Switching to fallback model '%s'.",
                            agent_name, model, next_fallback,
                        )
                        if on_model_switch:
                            try:
                                on_model_switch(model, next_fallback, reason)
                            except Exception as cb_err:
                                active_logger.warning("on_model_switch callback error: %s", cb_err)
                        break

                    # No more fallbacks and this model exhausted
                    break

                # Non-rate-limit errors: retry once if attempts remain, else raise
                if attempt < max_retries_per_model - 1:
                    time.sleep(1.0)
                    continue
                raise error

    # If we exited the models loop without returning, all models/retries were exhausted:
    if last_rate_limit_error is not None:
        if isinstance(last_rate_limit_error, RateLimitError):
            raise last_rate_limit_error
        clean_msg = sanitize_secret(str(last_rate_limit_error))
        raise RateLimitError(
            message=f"{agent_name} rate limit exceeded across models ({', '.join(models_to_try)}): {clean_msg}",
            response=getattr(last_rate_limit_error, "response", None),
            body=getattr(last_rate_limit_error, "body", None),
        ) from last_rate_limit_error

    if last_error is not None:
        raise last_error

    raise RuntimeError(f"{agent_name} failed to generate completion without an explicit exception.")

