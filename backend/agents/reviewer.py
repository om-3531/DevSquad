
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
You are reviewing proposed Python code for a software development workflow.

Task:
{task[:1000]}

Complete proposed code:
{code[:6000]}

Review instructions:
- Verify that the code directly satisfies the given task.
- Check every requirement in the task against the actual code.
- Reject code that implements unrelated functionality.
- Reject code with missing required functions or behavior.
- Check syntax, logic, edge cases, security, and error handling.
- Identify concrete bugs; do not invent issues.
- Do not approve code merely because it looks well-written.
- Do not claim tests were executed.
- If the task is ambiguous, identify the specific ambiguity.

Return REQUEST_CHANGES if any important requirement is missing.
If there are no concrete, important defects:
VERDICT: APPROVE
Issues: None
Fixes: None

If concrete, important defects exist:
VERDICT: REQUEST_CHANGES
Issues: [List up to 2 specific, verified defects]
Fixes: [Give a concrete fix for each defect]

Never include both verdicts in the same response.
"""

    def _has_verdict(self, content: str) -> bool:
        verdict = content.upper()
        return (
            "VERDICT: APPROVE" in verdict
            or "VERDICT: REQUEST_CHANGES" in verdict
        )
