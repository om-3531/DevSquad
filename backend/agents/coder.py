
import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()


class CoderAgent:
    def __init__(self):
        self.model = os.getenv("LLM_MODEL")
        api_key = os.getenv("LLM_API_KEY")

        if not api_key:
            raise ValueError("LLM_API_KEY is missing in .env")

        if not self.model:
            raise ValueError("LLM_MODEL is missing in .env")

        self.client = Groq(api_key=api_key)

    def generate_code(
        self,
        task: str,
        implementation_plan: str,
        existing_code: str = "",
    ) -> str:
        prompt = f"""
Generate a proposed Python code change for this task.

Task:
{task[:2000]}

Implementation plan:
{implementation_plan[:2000]}

Existing code:
{existing_code[:5000] or "No existing code provided."}

Rules:
- Return only the complete Python code.
- Do not use Markdown code fences.
- Do not include explanations outside the code.
- Include required imports.
- Do not claim tests were run or files were modified.
- Do not include secrets or API keys.
"""

        return self._generate(prompt)

    def improve_code(
        self,
        task: str,
        implementation_plan: str,
        previous_code: str,
        reviewer_feedback: str,
    ) -> str:
        prompt = f"""
Improve the proposed Python code using the reviewer feedback.

Task:
{task[:2000]}

Implementation plan:
{implementation_plan[:2000]}

Previous proposed code:
{previous_code[:5000]}

Reviewer feedback:
{reviewer_feedback[:2000]}

Rules:
- Fix the issues identified by the reviewer.
- Return the complete improved Python code only.
- Do not use Markdown code fences or explanations.
- Preserve existing functionality where possible.
- Do not claim tests were run or files were modified.
- Do not include secrets or API keys.
"""

        return self._generate(prompt)

    def _generate(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a careful Python software engineer. "
                        "Return complete Python code without Markdown "
                        "fences or explanations. Never claim to execute "
                        "code or modify repository files."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=3000,
        )

        content = response.choices[0].message.content

        if not content or not content.strip():
            raise ValueError(
                "Coder returned an empty response. Please retry."
            )

        return content.strip()
