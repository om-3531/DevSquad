import json
import logging
import os
import time

from dotenv import load_dotenv
from groq import Groq, RateLimitError

from backend.config import settings
from backend.agents.llm_text import (
    execute_chat_completion,
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


class PlannerAgent:
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
        return settings.get_model("planner")

    @model.setter
    def model(self, value: str | None):
        self._model = value

    @property
    def fallback_model(self) -> str | None:
        return getattr(self, "_fallback_model", None)

    @fallback_model.setter
    def fallback_model(self, value: str | None):
        self._fallback_model = value

    def create_plan(
        self,
        title: str,
        description: str,
        repository_context: str = "",
        repo_files: list[str] | None = None,
        target_path: str = "",
    ):
        file_tree_snippet = ""
        if repo_files:
            file_tree_snippet = "\nRepository structure (sample):\n" + "\n".join(f"- {p}" for p in repo_files[:12])
        target_hint = f"\nSpecified target file: {target_path}" if target_path else ""

        prompt = f"""
Create a structured implementation plan to solve this GitHub issue in the repository.

Title: {title[:200]}
Description: {(description or "No description")[:800]}
{target_hint}
{file_tree_snippet}
Repository context: {(repository_context or "Not provided")[:800]}

Return ONLY valid JSON with this exact structure:
{{
  "problem_summary": "One sentence summary of the issue",
  "acceptance_criteria": ["Criteria 1", "Criteria 2"],
  "target_files": [
    {{"path": "relative/path/to/file.py", "operation": "create or modify", "reason": "One short sentence why"}}
  ],
  "steps": ["Step 1", "Step 2", "Step 3"],
  "testing_strategy": "One sentence describing how to verify",
  "risks": ["One key risk"]
}}

Instructions:
- If a target file is specified, include it in target_files.
- If modifying an existing repository file, set operation to "modify".
- If creating a new file, set operation to "create".
- Provide clear, measurable acceptance criteria.
- Do not claim code was changed or tested.
"""
        extra_kwargs = get_model_extra_kwargs(self.model)

        def _on_switch(prev, next_m, reason):
            self.model = next_m
            if getattr(self, "on_fallback", None):
                self.on_fallback(prev, next_m, reason)

        response, used_model = execute_chat_completion(
            client=self.client,
            messages=[
                {
                    "role": "system",
                    "content": "Return concise, valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            primary_model=self.model,
            fallback_model=self.fallback_model,
            temperature=0.1,
            max_tokens=500,
            extra_kwargs=extra_kwargs,
            agent_name="Planner",
            max_retries_per_model=2,
            base_backoff=2.0,
            max_backoff=10.0,
            on_model_switch=_on_switch,
            logger=logger,
        )

        choice = response.choices[0]
        content = strip_code_fences(
            choice.message.content or ""
        )

        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {
                "problem_summary": (
                    "The AI returned a response that was not valid JSON."
                ),
                "raw_response": content[:1000],
            }