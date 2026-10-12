
import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()


class TesterAgent:
    __test__ = False

    def __init__(self):
        api_key = os.getenv("LLM_API_KEY")
        self.model = os.getenv("LLM_MODEL")

        if not api_key:
            raise ValueError("LLM_API_KEY is missing in .env")

        if not self.model:
            raise ValueError("LLM_MODEL is missing in .env")

        self.client = Groq(api_key=api_key)

    def generate_tests(self, task: str, proposed_code: str) -> str:
        prompt = f"""
Create pytest tests for the proposed Python code.

Task:
{task[:2000]}

Proposed code:
{proposed_code[:5000]}

Rules:
- Return Python test code only.
- Do not use Markdown code fences.
- Import functions from candidate.py using:
  from candidate import function_name
- Include normal input, edge cases, and expected output.
- Do not claim that tests were executed.
- Do not modify files.
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a careful software tester. "
                        "Return only valid Python pytest code."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.2,
            max_tokens=1200,
        )

        content = response.choices[0].message.content

        if not content or not content.strip():
            raise ValueError("Tester returned an empty response.")

        return content.strip()
