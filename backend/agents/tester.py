
import ast
import os
import time

from dotenv import load_dotenv
from groq import Groq

from backend.agents.llm_text import strip_code_fences

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
        prompt = self._build_prompt(task, proposed_code)
        last_error = None
        max_tokens = 2500
        previous_tests = ""

        for attempt in range(3):
            try:
                user_content = prompt
                if previous_tests:
                    user_content = (
                        f"{prompt}\n\n"
                        "Your previous answer was incomplete or invalid. "
                        "Return a complete, shorter pytest file from scratch. "
                        "Do not truncate functions or leave unfinished lines.\n\n"
                        "Previous incomplete output:\n"
                        f"{previous_tests[:1500]}"
                    )

                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You generate isolated pytest tests. "
                                "Follow the import and network restrictions "
                                "exactly. Return complete Python code only. "
                                "Never truncate the file."
                            ),
                        },
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.1,
                    max_tokens=max_tokens,
                    reasoning_effort="low",
                )

                choice = response.choices[0]
                content = choice.message.content
                finish_reason = getattr(choice, "finish_reason", None)

                if not content or not content.strip():
                    last_error = (
                        "Groq returned empty test content "
                        f"(finish_reason={finish_reason})."
                    )
                    max_tokens = min(max_tokens + 800, 4000)
                    time.sleep(1)
                    continue

                tests = strip_code_fences(content)
                previous_tests = tests

                if finish_reason == "length":
                    last_error = "Generated tests were truncated by the token limit."
                    max_tokens = min(max_tokens + 800, 4000)
                    time.sleep(1)
                    continue

                try:
                    tree = ast.parse(tests)
                except SyntaxError as error:
                    last_error = f"Generated tests have invalid syntax: {error}"
                    time.sleep(1)
                    continue

                forbidden_imports = self._forbidden_imports(tree)
                if forbidden_imports:
                    last_error = (
                        "Generated tests imported unavailable project "
                        "modules: " + ", ".join(forbidden_imports)
                    )
                    time.sleep(1)
                    continue

                return tests

            except Exception as error:
                last_error = (
                    f"Groq test generation failed: "
                    f"{type(error).__name__}: {error}"
                )
                if attempt < 2:
                    time.sleep(1)

        raise ValueError(
            "Tester could not generate valid isolated tests after "
            f"3 attempts. {last_error}"
        )

    def _build_prompt(self, task: str, proposed_code: str) -> str:
        return f"""
Generate a complete pytest file for the proposed Python code.

Task:
{task[:1200]}

Proposed code:
{proposed_code[:3000]}

Execution environment:
- proposed code is saved as candidate.py
- tests are saved as test_candidate.py
- only candidate.py and test_candidate.py are available
- pytest is installed
- no network access is available
- the full project and backend package are NOT available

Strict rules:
- Return valid Python pytest code only, without Markdown fences.
- Import functions or classes ONLY from candidate.py.
- Never import backend.main, backend.*, app, or other project modules.
- Never call real APIs, access the network, or read environment secrets.
- Do not use responses, requests-mock, or external mocking packages.
- Mock dependencies with unittest.mock when needed.
- Test the proposed code's actual public functions and classes.
- Include 2 to 4 complete tests covering normal inputs and one edge case.
- Every function must be fully closed. Never leave truncated code.
- If the code has no testable public behavior, write one complete
  test that checks its documented behavior without inventing APIs.
- Do not claim tests were executed.
"""

    def _forbidden_imports(self, tree: ast.AST) -> list[str]:
        forbidden = []
        blocked_roots = {"backend", "app"}

        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]

            for name in names:
                root = name.split(".", 1)[0]
                if root in blocked_roots:
                    forbidden.append(name)

        return forbidden
