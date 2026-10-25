import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Known model metadata for Groq models (limits, context windows, recommended roles)
KNOWN_MODEL_LIMITS: dict[str, dict[str, Any]] = {
    "openai/gpt-oss-120b": {
        "context_window": 8192,
        "tpm": 8000,
        "tpd": 200000,
        "description": "High-capacity reasoning model; best for complex coding.",
    },
    "openai/gpt-oss-20b": {
        "context_window": 8192,
        "tpm": 8000,
        "tpd": 200000,
        "description": "Balanced reasoning model; faster, good for tests/reviews.",
    },
    "llama-3.3-70b-versatile": {
        "context_window": 128000,
        "tpm": 12000,
        "tpd": 500000,
        "description": "High throughput, large context window; excellent generalist.",
    },
    "llama-3.1-8b-instant": {
        "context_window": 128000,
        "tpm": 20000,
        "tpd": 500000,
        "description": "Very fast, high rate limit; best for lightweight tasks.",
    },
    "qwen/qwen3.8-27b": {
        "context_window": 32768,
        "tpm": 8000,
        "tpd": 200000,
        "description": "Multilingual and coding capable model.",
    },
}


def sanitize_secret(text: str) -> str:
    """
    Scrub API keys, personal access tokens, and Bearer tokens from text
    to prevent accidental leaks in logs or user-facing messages.
    """
    if not text or not isinstance(text, str):
        return ""
    # Redact Groq keys (gsk_...)
    sanitized = re.sub(r"gsk_[A-Za-z0-9_-]{10,}", "[REDACTED_GROQ_KEY]", text)
    # Redact GitHub tokens (ghp_..., gho_..., github_pat_...)
    sanitized = re.sub(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}", "[REDACTED_GITHUB_TOKEN]", sanitized)
    sanitized = re.sub(r"github_pat_[A-Za-z0-9_]{30,}", "[REDACTED_GITHUB_TOKEN]", sanitized)
    # Redact Authorization: Bearer <token>
    sanitized = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/-]{10,}", r"\1[REDACTED_TOKEN]", sanitized, flags=re.I)
    # Redact key=... or api_key=... in query params
    sanitized = re.sub(r"(api[_-]?key=)[A-Za-z0-9._~+/-]{10,}", r"\1[REDACTED]", sanitized, flags=re.I)
    return sanitized


def get_available_models(client: Any) -> list[str]:
    """
    Query Groq's models.list endpoint to get currently active models for the account.
    Returns an empty list if client is not provided or the call fails.
    """
    if client is None:
        return []

    try:
        models_response = client.models.list()
        items = getattr(models_response, "data", [])
        available = []
        for m in items:
            model_id = getattr(m, "id", None)
            is_active = getattr(m, "active", True)
            if model_id and is_active:
                available.append(model_id)
        return available
    except Exception as err:
        logger.warning("Could not retrieve active models list from Groq: %s", sanitize_secret(str(err)))
        return []


def check_model_availability(client: Any, model_name: str) -> bool:
    """Check if model_name is active and available to the current Groq API key."""
    if not model_name:
        return False
    available = get_available_models(client)
    if available:
        return model_name in available
    # If list API is inaccessible (e.g. offline or strict firewall), check against known supported models
    return model_name in KNOWN_MODEL_LIMITS


def get_model_limits(model_name: str) -> dict[str, Any]:
    """Return known rate limit and context window metadata for model_name."""
    return KNOWN_MODEL_LIMITS.get(
        model_name,
        {
            "context_window": 8192,
            "tpm": 8000,
            "tpd": 200000,
            "description": "Standard Groq chat completion model.",
        },
    )


def recommend_fallback_model(
    client: Any,
    current_model: str,
    configured_fallback: str | None = None,
) -> str | None:
    """
    Recommend an alternative, available model when current_model hits rate limits.
    Validates model availability with Groq before recommending it.
    """
    available = get_available_models(client)

    # 1. Check configured fallback first
    if configured_fallback and configured_fallback != current_model:
        if not available or configured_fallback in available:
            return configured_fallback

    # 2. Candidate models prioritized by stability and capacity
    priority_candidates = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile",
        "qwen/qwen3.8-27b",
        "llama-3.1-8b-instant",
    ]

    for candidate in priority_candidates:
        if candidate == current_model:
            continue
        if not available or candidate in available:
            return candidate

    return configured_fallback if configured_fallback != current_model else None
