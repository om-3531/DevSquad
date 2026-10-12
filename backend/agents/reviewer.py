
from groq import Groq

from backend.config import settings


class ReviewerAgent:
    __test__ = False

    def __init__(self):
        if not settings.LLM_API_KEY:
            raise ValueError(
                "LLM_API_KEY is missing. Check your .env file."
            )

        self.client = Groq(api_key=settings.LLM_API_KEY)

    def review_code(self, task: str, code: str) -> str:
        prompt = f"""
Review the proposed code for the task.

Task:
{task[:1000]}

Code to review:
{code[:3000]}

Check for important bugs, security risks, error handling,
and edge cases.

Return ONLY this format:
Verdict: APPROVE or REQUEST_CHANGES
Issues: Maximum 2 short points
Fixes: Maximum 2 short points

Keep the response under 80 words.
Do not reproduce the full code.
Do not execute the code.
"""

        response = self.client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise code reviewer. "
                        "Review the supplied code without executing it. "
                        "Return a short verdict and actionable feedback."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.2,
            max_tokens=500,
            reasoning_effort="low",
        )

        choice = response.choices[0]
        content = choice.message.content

        if not content or not content.strip():
            return (
                "Verdict: REQUEST_CHANGES\n"
                "Issues: Reviewer returned an empty response.\n"
                "Fixes: Retry the review."
            )

        return content.strip()
