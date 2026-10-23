
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
Improve the proposed Python code using the reviewer feedback.

Task:
{task[:1200]}

Implementation plan:
{implementation_plan[:1000]}

Previous proposed code:
{previous_code[:3500]}

Reviewer feedback:
{reviewer_feedback[:1000]}

Rules:
- Fix the identified issues.
- Return complete, executable Python code only.
- Keep the output self-contained and focused on the task.
- Do not import backend, backend.main, or other project-specific modules.
- Do not generate multiple files or file-path headings.
- Preserve existing functionality where possible.
- Do not use Markdown code fences or explanations.
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
            temperature=0.1,
            max_tokens=6000,
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
