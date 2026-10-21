
import os
import time

from groq import Groq
from backend.config import settings
from backend.agents.llm_text import strip_code_fences


class ReviewerAgent:
    __test__ = False

    def __init__(self):
        if not settings.LLM_API_KEY:
            raise ValueError(
                "LLM_API_KEY is missing. Check your .env file."
            )

        self.client = Groq(api_key=settings.LLM_API_KEY)
        self.model = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")

    def review_code(self, task: str, code: str) -> str:
        last_error = "Reviewer returned an empty response."

        for attempt in range(2):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Review code critically. Return one verdict "
                            "line exactly as VERDICT: APPROVE or "
                            "VERDICT: REQUEST_CHANGES, then concise "
                            "issues and fixes. Never claim to execute code. "
                            "Never truncate the verdict line."
                        ),
                    },
                    {"role": "user", "content": self._build_prompt(task, code)},
                ],
                temperature=0.1,
                max_tokens=700,
                reasoning_effort="low",
            )

            choice = response.choices[0]
            content = strip_code_fences(choice.message.content or "")
            finish_reason = getattr(choice, "finish_reason", None)

            if content and self._has_verdict(content) and finish_reason != "length":
                return content

            last_error = (
                "Reviewer response was empty, truncated, or missing a verdict "
                f"(finish_reason={finish_reason})."
            )
            if attempt == 0:
                time.sleep(1)

        return (
            "VERDICT: REQUEST_CHANGES\n"
            f"Issues: {last_error}\n"
            "Fixes: Retry the review with a complete verdict."
        )

    def _build_prompt(self, task: str, code: str) -> str:
        return f"""
Review this proposed Python code.

Task:
{task[:700]}

Code:
{code[:2200]}

Check correctness, security, error handling, and task requirements.

Return exactly:
VERDICT: APPROVE
or
VERDICT: REQUEST_CHANGES

Then:
Issues: Up to 2 short points
Fixes: Up to 2 short points

Approve only if no important issue remains.
Do not claim tests were executed.
Keep feedback concise and complete.
"""

    def _has_verdict(self, content: str) -> bool:
        verdict = content.upper()
        return (
            "VERDICT: APPROVE" in verdict
            or "VERDICT: REQUEST_CHANGES" in verdict
        )
