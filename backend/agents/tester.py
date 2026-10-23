
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
Generate a complete, reliable pytest file for the proposed Python code.

Task:
{task[:1200]}

Proposed code:
{proposed_code[:4000]}

Execution environment:
- Implementation is saved as candidate.py.
- Tests are saved as test_candidate.py.
- Only candidate.py and test_candidate.py are available.
- pytest and the Python standard library are installed.
- Network access is disabled.
- The full project and backend package are unavailable.

Strict requirements:
- Return valid Python pytest code only, without Markdown fences.
- Import code under test ONLY from candidate.py.
- Never import backend, backend.main, app, or other project modules.
- Never make real network requests or call external APIs.
- Never access environment variables, credentials, or secrets.
- Use unittest.mock to mock network calls and external dependencies.
- Tests must be deterministic and run without internet or credentials.
- Do not assume undocumented functions, classes, arguments, or behavior.
- Test only functions and classes that actually exist in candidate.py.
- Check normal behavior, error handling, and edge cases when applicable.
- For HTTPError tests, construct a realistic response body using io.BytesIO
  if the implementation reads the error body.
- Assert only behavior supported by the implementation or task requirements.
- Do not modify candidate.py from the tests.
- Do not claim tests were executed.
- Ensure every import, mock, function, and parenthesis is complete.
- Keep tests concise to avoid token-limit truncation.

If the code has no testable public behavior, create one complete test
for behavior explicitly documented in the code. Do not invent APIs.
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
