
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
Create three concise pytest test cases.

Task: {task}

Code:
{proposed_code}

Include normal input, edge case, and expected output.
Do not claim tests were executed.
Keep the answer short.
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a careful software tester.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.2,
            max_tokens=1200,
        )

        choice = response.choices[0]
        content = choice.message.content

        print("Finish reason:", choice.finish_reason)

        if not content or not content.strip():
            raise ValueError(
                "Empty AI response. "
                f"Finish reason: {choice.finish_reason}"
            )

        return content
