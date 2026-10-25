import ast
import logging
import os
import time

from dotenv import load_dotenv
from groq import Groq, RateLimitError

from backend.config import settings
from backend.agents.llm_text import (
    extract_retry_after,
    get_model_extra_kwargs,
    is_daily_limit_error,
    is_rate_limit_error,
    safe_log_completion,
    safe_log_rate_limit,
    strip_code_fences,
)
from backend.agents.model_manager import sanitize_secret

load_dotenv()

logger = logging.getLogger(__name__)


class CoderAgent:
    __test__ = False

    def __init__(self):
        self._model = None
        api_key = settings.LLM_API_KEY or os.getenv("LLM_API_KEY")

        if not api_key:
            raise ValueError("LLM_API_KEY is missing in .env")

        self.client = Groq(api_key=api_key)

    @property
    def model(self) -> str:
        if self._model:
            return self._model
        return settings.get_model("coder")

    @model.setter
    def model(self, value: str | None):
        self._model = value

    def generate_code(
        self,
        task: str,
        implementation_plan: str,
        existing_code: str = "",
        operation: str = "create",
        file_path: str = "",
        acceptance_criteria: list[str] | None = None,
    ) -> str:
        existing_block = ""
        if existing_code.strip():
            existing_block = (
                f"\nORIGINAL / EXISTING FILE CONTENT ({file_path or 'target'}):\n"
                f"{existing_code}\n"
            )

        criteria_block = ""
        if acceptance_criteria:
            criteria_block = "\nACCEPTANCE CRITERIA:\n" + "\n".join(f"- {c}" for c in acceptance_criteria)

        op_instruction = ""
        if operation == "modify":
            op_instruction = (
                f"- OPERATION IS 'MODIFY' FOR '{file_path}'. You MUST PRESERVE all existing classes, "
                "methods, functions, and logic from the original file. "
                "Do NOT delete or replace existing methods with placeholders or new classes. "
                "Retain every existing class and method verbatim and add the newly required methods to the existing class.\n"
            )
        else:
            op_instruction = (
                f"- OPERATION IS 'CREATE' FOR NEW FILE '{file_path or 'solution.py'}'. "
                "Return complete, production-ready implementation code.\n"
            )

        prompt = f"""
Implement the requested Python task. Return one complete, executable Python module.

TASK:
{task[:1000]}

TARGET FILE: {file_path or 'Not specified'} (Operation: {operation.upper()})
{criteria_block}

IMPLEMENTATION PLAN:
{implementation_plan[:800]}
{existing_block}
REQUIREMENTS:
{op_instruction}- Implement real, working Python code that solves the task.
- Do not build mock pipelines, placeholder classes, or unrelated test frameworks.
- Keep the implementation clean, self-contained, and robust.
- Include necessary imports and structured error handling.
- When handling HTTP requests, check numeric status codes (e.g. `resp.status_code == 404`) rather than substring matching on exception strings.
- Handle HTTP 404 (Not Found) separately from 401/403 auth, 429 rate limits, and 500 server errors.
- Do not import backend, app, or other project-specific modules unless modifying an existing project file that imports them.
- Use Python standard-library modules or approved dependencies (e.g. requests).
- Do not include tests in the implementation. Tests are generated separately.
- Do not generate multiple files, file-path headings, or Markdown code fences.
- Return complete, valid Python code only.
"""
        return self._generate(prompt)

    def improve_code(
        self,
        task: str,
        implementation_plan: str,
        previous_code: str,
        reviewer_feedback: str,
        existing_code: str = "",
        operation: str = "create",
        file_path: str = "",
        acceptance_criteria: list[str] | None = None,
    ) -> str:
        existing_block = ""
        if existing_code.strip():
            existing_block = (
                f"\nORIGINAL / EXISTING FILE CONTENT ({file_path or 'target'}):\n"
                f"{existing_code}\n"
            )

        criteria_block = ""
        if acceptance_criteria:
            criteria_block = "\nACCEPTANCE CRITERIA:\n" + "\n".join(f"- {c}" for c in acceptance_criteria)

        op_instruction = ""
        if operation == "modify":
            op_instruction = (
                f"- OPERATION IS 'MODIFY' FOR '{file_path}'. You MUST PRESERVE all existing classes, "
                "methods, functions, and logic from the original file. "
                "Do NOT delete or replace existing methods with placeholders or new classes. "
                "Retain every existing class and method verbatim and add the newly required methods to the existing class.\n"
            )

        prompt = f"""
Revise the Python implementation to address the review feedback and test failures.
Return one complete, executable Python module only.

TASK:
{task[:1000]}

TARGET FILE: {file_path or 'Not specified'} (Operation: {operation.upper()})
{criteria_block}

IMPLEMENTATION PLAN:
{implementation_plan[:800]}
{existing_block}
PREVIOUS CODE:
{previous_code}

REVIEWER FEEDBACK / TEST RESULTS:
{reviewer_feedback[:1200]}

REQUIREMENTS:
{op_instruction}- Implement the actual task and directly resolve each issue mentioned in the feedback.
- Do not generate mock pipelines, placeholder classes, or unrelated test harnesses.
- Write the actual implementation requested by the task.
- Keep the implementation clean, self-contained, and robust.
- Include required imports and structured error handling.
- When handling HTTP requests, check numeric status codes (e.g. `resp.status_code == 404`) rather than substring matching on exception strings.
- Handle HTTP 404 (Not Found) separately from 401/403 auth, 429 rate limits, and 500 server errors.
- Do not import backend, app, or other project-specific modules unless modifying an existing file.
- Use Python standard-library modules or approved dependencies (e.g. requests).
- Do not include tests, multiple files, file headings, or Markdown fences.
- Return complete, valid Python code only.
"""
        return self._generate(prompt)

    def _generate(self, prompt: str) -> str:
        last_error = "Coder returned an empty response."
        user_content = prompt
        max_attempts = 3
        base_backoff = 2.0
        max_backoff = 10.0
        extra_kwargs = get_model_extra_kwargs(self.model)

        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an expert Python software engineer. "
                                "Return complete, syntactically valid Python code only. "
                                "Do not include Markdown fences, explanations, or commentary. "
                                "Never truncate output and never claim to execute code."
                            ),
                        },
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.1,
                    max_tokens=4000,
                    **extra_kwargs,
                )

                if not response.choices:
                    last_error = "Coder returned no choices from LLM."
                    logger.warning("Coder received empty choices list on attempt %d", attempt + 1)
                    if attempt < max_attempts - 1:
                        time.sleep(1)
                    continue

                choice = response.choices[0]
                content = choice.message.content or ""
                finish_reason = getattr(choice, "finish_reason", None)
                usage = getattr(response, "usage", None)

                # Safely log metadata without exposing secrets
                safe_log_completion(logger, self.model, finish_reason, content, usage)

                clean_code = strip_code_fences(content).strip()

                # Handle token length truncation first regardless of whether content is empty or partial
                if finish_reason == "length":
                    if not clean_code:
                        last_error = "Coder output was truncated by the token limit before code generation."
                        logger.warning(
                            "Coder output truncated before generating code (empty response): model=%s finish_reason=%s attempt=%d",
                            self.model,
                            finish_reason,
                            attempt + 1,
                        )
                        user_content = (
                            f"{prompt[:1200]}\n\n"
                            "IMPORTANT: Your previous output was truncated before producing code. "
                            "Return a minimal, concise Python implementation under 100 lines. "
                            "Omit verbose docstrings and commentary. Return valid Python code only."
                        )
                    else:
                        last_error = "Coder output was truncated by the token limit."
                        logger.warning(
                            "Coder output truncated mid-code: model=%s finish_reason=%s content_length=%d attempt=%d",
                            self.model,
                            finish_reason,
                            len(clean_code),
                            attempt + 1,
                        )
                        user_content = (
                            f"{prompt[:1200]}\n\n"
                            "IMPORTANT: Your previous output was truncated mid-code. "
                            "Return a more concise, complete implementation that fits "
                            "within the token limit without omitting any required logic."
                        )
                    if attempt < max_attempts - 1:
                        time.sleep(1)
                    continue

                if not clean_code:
                    last_error = f"Coder returned an empty response (finish_reason={finish_reason})."
                    logger.warning(
                        "Coder returned empty response: model=%s finish_reason=%s attempt=%d",
                        self.model,
                        finish_reason,
                        attempt + 1,
                    )
                    user_content = (
                        f"{prompt[:1200]}\n\n"
                        "IMPORTANT: Return complete, valid Python code directly."
                    )
                    if attempt < max_attempts - 1:
                        time.sleep(1)
                    continue

                # Verify syntax deterministically before accepting the code
                try:
                    ast.parse(clean_code)
                except SyntaxError as syntax_err:
                    last_error = f"Coder generated invalid Python syntax: {syntax_err}"
                    logger.warning(
                        "Coder generated invalid Python syntax at line %s: %s",
                        syntax_err.lineno,
                        syntax_err.msg,
                    )
                    user_content = (
                        f"{prompt[:1200]}\n\n"
                        f"IMPORTANT: Your previous code had a Python syntax error: {syntax_err}. "
                        "Fix the syntax error and return valid Python code only."
                    )
                    if attempt < max_attempts - 1:
                        time.sleep(1)
                    continue

                return clean_code

            except Exception as err:
                if is_rate_limit_error(err):
                    retry_after = extract_retry_after(err)
                    safe_log_rate_limit(logger, err, self.model, retry_after)
                    # If daily quota exhausted or retry delay exceeds max backoff, do not retry repeatedly
                    if is_daily_limit_error(err) or (retry_after and retry_after > max_backoff):
                        if isinstance(err, RateLimitError):
                            raise
                        raise RateLimitError(
                            message=f"Coder rate limit exceeded: {sanitize_secret(str(err))}",
                            response=getattr(err, "response", None),
                            body=getattr(err, "body", None),
                        ) from err

                    if attempt < max_attempts - 1:
                        delay = min(
                            max((retry_after or (base_backoff * (2**attempt))) + 0.5, 1.0),
                            max_backoff,
                        )
                        logger.warning("Rate limit on Coder attempt %d; sleeping %.1fs", attempt + 1, delay)
                        time.sleep(delay)
                        continue
                    if isinstance(err, RateLimitError):
                        raise
                    raise RateLimitError(
                        message=f"Coder rate limit exceeded: {sanitize_secret(str(err))}",
                        response=getattr(err, "response", None),
                        body=getattr(err, "body", None),
                    ) from err

                last_error = f"{type(err).__name__}: {sanitize_secret(str(err))}"
                logger.error("Error in Coder attempt %d: %s", attempt + 1, sanitize_secret(str(err)))
                if attempt < max_attempts - 1:
                    time.sleep(1)
                    continue
                raise

        raise ValueError(f"Code generation failed: {last_error}")

