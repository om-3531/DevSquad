
import json
import os

from dotenv import load_dotenv
from groq import Groq

from backend.agents.llm_text import strip_code_fences

load_dotenv()


class PlannerAgent:
    def __init__(self):
        api_key = os.getenv("LLM_API_KEY")
        self.model = os.getenv("LLM_MODEL")

        if not api_key:
            raise ValueError("LLM_API_KEY is missing in .env")
        if not self.model:
            raise ValueError("LLM_MODEL is missing in .env")

        self.client = Groq(api_key=api_key)

    def create_plan(
        self,
        title: str,
        description: str,
        repository_context: str = "",
    ):
        prompt = f"""
Create a concise implementation plan for this GitHub issue.

Title: {title[:200]}
Description: {(description or "No description")[:1200]}
Repository context: {(repository_context or "Not provided")[:1500]}

Return ONLY JSON:
{{
  "problem_summary": "One short sentence",
  "steps": ["Step 1", "Step 2", "Step 3"],
  "testing_strategy": "One short sentence",
  "risks": ["One key risk"]
}}

Do not claim code was changed or tested.
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "Return concise, valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        content = strip_code_fences(response.choices[0].message.content or "")

        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {
                "problem_summary": "The AI returned a response that was not valid JSON.",
                "raw_response": content[:1000],
            }