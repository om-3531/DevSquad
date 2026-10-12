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

Check:
- Correctness and important bugs
- Security risks
- Error handling and edge cases
- Whether the proposed code actually addresses the task

Return exactly one verdict line:
VERDICT: APPROVE
or
VERDICT: REQUEST_CHANGES

Then return:
Issues: Up to 2 short points
Fixes: Up to 2 short points

Rules:
- APPROVE only when no important issue remains.
- Use REQUEST_CHANGES when important problems remain.
- Do not approve code just because it looks plausible.
- Do not execute the code.
- Do not claim tests were run.
- Keep the response under 100 words.
"""

        response = self.client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a careful code reviewer. "
                        "Return one unambiguous verdict using the "
                        "required format. Never claim to execute code."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.1,
            max_tokens=500,
            reasoning_effort="low",
        )

        content = response.choices[0].message.content

        if not content or not content.strip():
            return (
                "VERDICT: REQUEST_CHANGES\n"
                "Issues: Reviewer returned an empty response.\n"
                "Fixes: Retry the review."
            )

        return content.strip()