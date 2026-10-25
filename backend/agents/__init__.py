
import os
import json

from dotenv import load_dotenv
from groq import Groq


load_dotenv()


class PlannerAgent:
    def __init__(self):
        api_key = os.getenv("LLM_API_KEY")
        self.model = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")

        if not api_key:
            raise ValueError("LLM_API_KEY is missing in .env")

        self.client = Groq(api_key=api_key)

    def create_plan(self, title: str, description: str):
        prompt = f"""
You are the Planner Agent in DevSquad, an AI software development team.

Analyze this GitHub issue and create a practical implementation plan.

Issue title:
{title}

Issue description:
{description or "No description provided."}

Return ONLY valid JSON in this format:
{{
  "problem_summary": "Short explanation of the problem",
  "steps": [
    "First implementation step",
    "Second implementation step",
    "Third implementation step"
  ],
  "testing_strategy": "How to test the changes",
  "risks": [
    "Possible risk"
  ]
}}

Do not claim that you have modified or tested any code.
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You create clear and safe software implementation plans."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2,
        )

        content = response.choices[0].message.content

        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {
                "problem_summary": "The AI returned a response that was not valid JSON.",
                "raw_response": content,
            }
