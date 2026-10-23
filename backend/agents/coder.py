
import os

from dotenv import load_dotenv
from groq import Groq

from backend.agents.llm_text import strip_code_fences

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
{task[:1200]}

Implementation plan:
{implementation_plan[:1000]}

Existing code:
{existing_code[:3000] or "No existing code provided."}

Rules:
- Return complete, executable Python code only.
- The output will be saved as candidate.py and tested in an isolated Docker container.
- Keep the output self-contained and focused on the task.
- Do not import backend, backend.main, or other project-specific modules.
- Do not generate multiple files or file-path headings.
- Use only standard-library modules and dependencies available in the test environment.
- Expose clear functions or classes that can be tested.
- Include required imports.
- Do not use Markdown code fences or explanations.
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
You are implementing a real GitHub issue in a Python project.

GITHUB ISSUE / TASK:
{task[:2000]}

IMPLEMENTATION PLAN:
{implementation_plan[:1500]}

RELEVANT EXISTING CODE:
{existing_code[:4000] or "No existing code provided."}

STRICT REQUIREMENTS:
1. Read the task carefully and implement exactly what it requests.
2. Do not invent unrelated features or build a different application.
3. Follow the implementation plan when it matches the task.
4. If existing code is provided, preserve its relevant functionality.
5. Return complete, executable Python code.
6. Include required imports and appropriate error handling.
7. Do not import backend or other project-specific modules.
8. Do not include tests in the production code.
9. Do not return multiple files or file-path headings.
10. Do not use Markdown fences or explanations.
11. Never claim that code was executed or files were modified.
12. Never include secrets or API keys.

Before responding, verify internally that the code directly addresses
the task and that every requested requirement is implemented.

Return Python code only.
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
            temperature=0.1,
            max_tokens=8000,
        )

        choice = response.choices[0]
        content = strip_code_fences(choice.message.content or "")
        finish_reason = getattr(choice, "finish_reason", None)

        if not content:
            raise ValueError(
                "Coder returned an empty response. Please retry."
            )

        if finish_reason == "length":
            raise ValueError(
                "Coder output was truncated. Please retry with a smaller task."
            )

        return content
