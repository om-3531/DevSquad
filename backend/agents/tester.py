import ast
import logging
import os
import re
import time

from dotenv import load_dotenv
from groq import Groq, RateLimitError

from backend.config import settings
from backend.agents.llm_text import (
    extract_retry_after,
    get_model_extra_kwargs,
    is_daily_limit_error,
    is_rate_limit_error,
    safe_log_rate_limit,
    strip_code_fences,
)
from backend.agents.model_manager import sanitize_secret

load_dotenv()
logger = logging.getLogger(__name__)


class TesterAgent:
    __test__ = False

    def __init__(self, fallback_model: str | None = None):
        self._model = None
        self._fallback_model = fallback_model or settings.LLM_FALLBACK_MODEL
        self.on_fallback = None
        api_key = settings.LLM_API_KEY or os.getenv("LLM_API_KEY")

        if not api_key:
            raise ValueError("LLM_API_KEY is missing in .env")

        self.client = Groq(api_key=api_key)

    @property
    def model(self) -> str:
        if self._model:
            return self._model
        return settings.get_model("tester")

    @model.setter
    def model(self, value: str | None):
        self._model = value

    @property
    def fallback_model(self) -> str | None:
        return getattr(self, "_fallback_model", None)

    @fallback_model.setter
    def fallback_model(self, value: str | None):
        self._fallback_model = value
        self._model = value

    def generate_tests(self, task: str, proposed_code: str) -> str:
        models_to_try = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models_to_try.append(self.fallback_model)

        prompt = self._build_prompt(task, proposed_code)
        last_error = None
        max_tokens = 1200
        max_attempts_per_model = 3
        base_backoff = 2.0
        max_backoff = 15.0

        for model_idx, current_model in enumerate(models_to_try):
            has_fallback = (model_idx + 1 < len(models_to_try))
            next_model = models_to_try[model_idx + 1] if has_fallback else None
            user_content = prompt
            extra_kwargs = get_model_extra_kwargs(current_model)

            for attempt in range(max_attempts_per_model):
                try:
                    response = self.client.chat.completions.create(
                        model=current_model,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You generate isolated pytest tests. "
                                    "Import code under test only from candidate.py. "
                                    "Never import backend or app modules. "
                                    "Mock all network calls. "
                                    "Return complete, concise Python code only."
                                ),
                            },
                            {"role": "user", "content": user_content},
                        ],
                        temperature=0.1,
                        max_tokens=max_tokens,
                        **extra_kwargs,
                    )

                    choice = response.choices[0]
                    content = choice.message.content
                    finish_reason = getattr(choice, "finish_reason", None)

                    if not content or not content.strip():
                        last_error = (
                            "Groq returned empty test content "
                            f"(finish_reason={finish_reason})."
                        )
                        if attempt < max_attempts_per_model - 1:
                            time.sleep(1)
                        continue

                    tests = strip_code_fences(content)
                    # Ensure no invalid builtins.validate_repository calls are generated
                    tests = re.sub(
                        r'patch\.object\s*\(\s*builtins\s*,\s*["\']validate_repository["\']',
                        'patch("candidate.validate_repository"',
                        tests,
                    )
                    tests = re.sub(
                        r'patch\s*\(\s*["\']builtins\.validate_repository["\']',
                        'patch("candidate.validate_repository"',
                        tests,
                    )
                    tests = re.sub(
                        r'builtins\.validate_repository',
                        'candidate.validate_repository',
                        tests,
                    )
                    tests = re.sub(
                        r'patch\.object\s*\(\s*validate_repository\s*,\s*["\']__call__["\']',
                        'patch("candidate.validate_repository"',
                        tests,
                    )

                    if finish_reason == "length":
                        last_error = "Generated tests were truncated by the token limit."
                        user_content = (
                            f"{prompt}\n\n"
                            "NOTE: Your previous output was truncated. Generate a shorter, "
                            "more concise test suite covering only the core requirements."
                        )
                        if attempt < max_attempts_per_model - 1:
                            time.sleep(1)
                        continue

                    try:
                        tree = ast.parse(tests)
                    except SyntaxError as error:
                        last_error = f"Generated tests have invalid syntax: {error}"
                        user_content = (
                            f"{prompt}\n\n"
                            "NOTE: Your previous output had invalid Python syntax. "
                            "Return syntactically valid pytest code only."
                        )
                        if attempt < max_attempts_per_model - 1:
                            time.sleep(1)
                        continue

                    forbidden_imports = self._forbidden_imports(tree)
                    if forbidden_imports:
                        last_error = (
                            "Generated tests imported unavailable project "
                            "modules: " + ", ".join(forbidden_imports)
                        )
                        user_content = (
                            f"{prompt}\n\n"
                            "NOTE: Do not import backend or app modules. "
                            "Import code under test ONLY from candidate.py."
                        )
                        if attempt < max_attempts_per_model - 1:
                            time.sleep(1)
                        continue

                    self.model = current_model
                    return tests

                except Exception as error:
                    if is_rate_limit_error(error):
                        retry_after = extract_retry_after(error)
                        is_daily = is_daily_limit_error(error)
                        safe_log_rate_limit(logger, error, current_model, retry_after)

                        # If daily quota exhausted or retry delay exceeds max backoff, switch to fallback model immediately
                        if (is_daily or (retry_after and retry_after > max_backoff)) and has_fallback:
                            reason = "daily_quota_exhausted" if is_daily else f"retry_delay_{retry_after:.1f}s_exceeds_max_backoff"
                            logger.warning(
                                "Tester: Rate limit on model '%s' (%s). Switching to fallback model '%s'.",
                                current_model,
                                reason,
                                next_model,
                            )
                            self.model = next_model
                            if getattr(self, "on_fallback", None):
                                try:
                                    self.on_fallback(current_model, next_model, reason)
                                except Exception as cb_err:
                                    logger.warning("on_fallback callback error: %s", cb_err)
                            break

                        if attempt < max_attempts_per_model - 1:
                            if retry_after is not None:
                                delay = min(max(retry_after + 0.5, 1.0), max_backoff)
                            else:
                                delay = min(base_backoff * (2 ** attempt), max_backoff)
                            time.sleep(delay)
                            continue

                        # Model exhausted its attempts
                        if has_fallback:
                            reason = f"exhausted_{max_attempts_per_model}_attempts"
                            logger.warning(
                                "Tester: Exhausted %d attempts for model '%s'. Switching to fallback model '%s'.",
                                max_attempts_per_model,
                                current_model,
                                next_model,
                            )
                            self.model = next_model
                            if getattr(self, "on_fallback", None):
                                try:
                                    self.on_fallback(current_model, next_model, reason)
                                except Exception as cb_err:
                                    logger.warning("on_fallback callback error: %s", cb_err)
                            break

                        # On final attempt exhausted on rate limit without fallback, raise RateLimitError
                        if isinstance(error, RateLimitError):
                            raise
                        raise RateLimitError(
                            message=f"Groq rate limit exceeded after {max_attempts_per_model} attempts: {sanitize_secret(str(error))}",
                            response=getattr(error, "response", None),
                            body=getattr(error, "body", None),
                        ) from error

                    last_error = (
                        f"Groq test generation failed: "
                        f"{type(error).__name__}: {sanitize_secret(str(error))}"
                    )
                    if attempt < max_attempts_per_model - 1:
                        time.sleep(1)

        raise ValueError(
            f"Tester could not generate valid isolated tests after {max_attempts_per_model} attempts. {last_error}"
        )

    def _extract_retry_after(self, error: Exception) -> float | None:
        response = getattr(error, "response", None)
        if response is not None:
            headers = getattr(response, "headers", {})
            for key in ("retry-after", "Retry-After", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
                val = headers.get(key)
                if val:
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        pass

        match = re.search(r"try again in (\d+(?:\.\d+)?)\s*s", str(error), re.I)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, TypeError):
                pass

        return None

    def _build_prompt(self, task: str, proposed_code: str) -> str:
        return f"""
Generate a concise, reliable pytest file for the proposed Python code.

TASK:
{task[:600]}

PROPOSED CODE (candidate.py):
{proposed_code}

REQUIREMENTS:
- Return valid pytest Python code only, without Markdown fences or explanations.
- Import code under test ONLY from candidate.py (e.g. from candidate import ...).
- Do not import backend, app, or project-specific modules.
- Never import builtins or patch builtins.validate_repository. Validate invalid repositories by passing invalid strings directly (e.g. repo='invalid-repo' without a slash).
- If mocking validate_repository, patch 'candidate.validate_repository', NEVER builtins.
- Mock all external network calls using unittest.mock; network access is disabled.
- Ensure tests are deterministic and do not require internet or credentials.
- Test only functions and classes that exist in candidate.py.
- If testing context managers (e.g. urllib.request.urlopen), use MagicMock.
- Keep tests concise to respect token limits.
"""

    def _forbidden_imports(self, tree: ast.AST) -> list[str]:
        forbidden = []
        blocked_roots = {"backend", "app"}

        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]

            for name in names:
                root = name.split(".", 1)[0]
                if root in blocked_roots:
                    forbidden.append(name)

        return forbidden
