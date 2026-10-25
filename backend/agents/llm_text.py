import logging
import re
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
