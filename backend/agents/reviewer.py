import ast
import os
import time

from groq import Groq, RateLimitError
from backend.config import settings
from backend.agents.llm_text import (
    extract_retry_after,
    get_model_extra_kwargs,
    is_daily_limit_error,
    is_rate_limit_error,
    safe_log_rate_limit,
    strip_code_fences,
)
from backend.agents.model_manager import sanitize_secret


class ReviewerAgent:
    __test__ = False

    def __init__(self):
        self._model = None
        if not settings.LLM_API_KEY:
            raise ValueError(
                "LLM_API_KEY is missing. Check your .env file."
            )

        self.client = Groq(api_key=settings.LLM_API_KEY)

    @property
    def model(self) -> str:
        if self._model:
            return self._model
        return settings.get_model("reviewer")

    @model.setter
    def model(self, value: str | None):
        self._model = value

    def review_code(
        self,
        task: str,
        code: str,
        plan: str = "",
        target_path: str = "",
        existing_code: str = "",
        operation: str = "create",
        acceptance_criteria: list[str] | None = None,
        test_code: str = "",
        test_path: str = "",
    ) -> str:
        # Auto-discover associated test file if test_code is not provided
        if not test_code and target_path:
            try:
                from pathlib import Path
                target_p = Path(target_path)
                root = Path(__file__).resolve().parent.parent.parent
                candidates = [
                    root / f"test_{target_p.stem}.py",
                    root / f"test_{target_p.parent.name}_{target_p.stem}.py",
                ]
                if "github" in target_path or "issues" in target_path or "client" in target_path:
                    candidates.append(root / "test_github_client.py")
                    candidates.append(root / "test_issue_api.py")
                for c in candidates:
                    if c.exists():
                        test_path = c.name
                        with open(c, "r", encoding="utf-8") as f:
                            test_code = f.read()
                        break
            except Exception:
                pass

        # Deterministic AST / syntax check before expensive LLM call
        try:
            new_tree = ast.parse(code)
        except SyntaxError as syntax_err:
            return (
                "VERDICT: REQUEST_CHANGES\n"
                f"Issues:\n1. Python syntax error at line {syntax_err.lineno}: {syntax_err.msg}.\n"
                "Fixes:\n1. Fix the syntax error so that ast.parse succeeds."
            )

        # Deterministic preservation check when operation is modify
        if operation == "modify" and existing_code:
            try:
                orig_tree = ast.parse(existing_code)
                orig_classes = {
                    n.name: {m.name for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
                    for n in orig_tree.body if isinstance(n, ast.ClassDef)
                }
                new_classes = {
                    n.name: {m.name for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
                    for n in new_tree.body if isinstance(n, ast.ClassDef)
                }

                missing_classes = set(orig_classes.keys()) - set(new_classes.keys())
                missing_methods = []
                for cls_name, orig_methods in orig_classes.items():
                    if cls_name in new_classes:
                        diff = orig_methods - new_classes[cls_name]
                        if diff:
                            missing_methods.extend([f"{cls_name}.{m}" for m in sorted(diff)])

                if missing_classes or missing_methods:
                    missing_desc = []
                    if missing_classes:
                        missing_desc.append(f"classes: {', '.join(sorted(missing_classes))}")
                    if missing_methods:
                        missing_desc.append(f"methods: {', '.join(missing_methods)}")
                    return (
                        "VERDICT: REQUEST_CHANGES\n"
                        f"Issues:\n1. Modification deleted existing definitions ({'; '.join(missing_desc)}). Original codebase must be preserved.\n"
                        "Fixes:\n1. Retain all original classes and methods in the file, and only append or modify the required methods."
                    )
            except Exception:
                pass

        last_error = "Reviewer returned an empty response."
        max_attempts = 2
        base_backoff = 2.0
        max_backoff = 10.0

        extra_kwargs = get_model_extra_kwargs(self.model)

        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Review code critically. Return one verdict "
                                "line exactly as VERDICT: APPROVE or "
                                "VERDICT: REQUEST_CHANGES, then concise "
                                "issues and fixes. Never claim to execute code. "
                                "Never truncate the verdict line."
                            ),
                        },
                        {
                            "role": "user",
                            "content": self._build_prompt(
                                task=task,
                                code=code,
                                plan=plan,
                                target_path=target_path,
                                existing_code=existing_code,
                                operation=operation,
                                acceptance_criteria=acceptance_criteria,
                                test_code=test_code,
                                test_path=test_path,
                            ),
                        },
                    ],
                    temperature=0.1,
                    max_tokens=700,
                    **extra_kwargs,
                )

                choice = response.choices[0]
                content = strip_code_fences(choice.message.content or "")
                finish_reason = getattr(choice, "finish_reason", None)

                if content and self._has_verdict(content) and finish_reason != "length":
                    return content

                last_error = (
                    "Reviewer response was empty, truncated, or missing a verdict "
                    f"(finish_reason={finish_reason})."
                )
                if attempt < max_attempts - 1:
                    time.sleep(1)

            except Exception as error:
                if is_rate_limit_error(error):
                    retry_after = extract_retry_after(error)
                    if is_daily_limit_error(error) or (retry_after and retry_after > max_backoff):
                        if isinstance(error, RateLimitError):
                            raise
                        raise RateLimitError(
                            message=f"Reviewer rate limit exceeded: {sanitize_secret(str(error))}",
                            response=getattr(error, "response", None),
                            body=getattr(error, "body", None),
                        ) from error

                    if attempt < max_attempts - 1:
                        delay = min(
                            max((retry_after or (base_backoff * (2**attempt))) + 0.5, 1.0),
                            max_backoff,
                        )
                        time.sleep(delay)
                        continue
                    if isinstance(error, RateLimitError):
                        raise
                    raise RateLimitError(
                        message=f"Reviewer rate limit exceeded: {sanitize_secret(str(error))}",
                        response=getattr(error, "response", None),
                        body=getattr(error, "body", None),
                    ) from error

                last_error = f"{type(error).__name__}: {sanitize_secret(str(error))}"
                if attempt < max_attempts - 1:
                    time.sleep(1)
                    continue
                raise

        return (
            "VERDICT: REQUEST_CHANGES\n"
            f"Issues: {last_error}\n"
            "Fixes: Retry the review with a complete verdict."
        )

    def _build_prompt(
        self,
        task: str,
        code: str,
        plan: str = "",
        target_path: str = "",
        existing_code: str = "",
        operation: str = "create",
        acceptance_criteria: list[str] | None = None,
        test_code: str = "",
        test_path: str = "",
    ) -> str:
        target_info = f"\nTarget file: {target_path} (Operation: {operation.upper()})" if target_path else ""
        plan_info = f"\nImplementation plan:\n{plan[:600]}" if plan else ""
        criteria_info = ""
        if acceptance_criteria:
            criteria_info = "\nAcceptance criteria:\n" + "\n".join(f"- {c}" for c in acceptance_criteria)

        test_info = ""
        if test_code:
            test_info = f"\nAssociated test file ({test_path or 'tests'}):\n{test_code[:2500]}\n"

        op_check = ""
        if operation == "modify":
            op_check = "- Verify that existing unrelated functions and classes were preserved and not deleted.\n"

        return f"""
You are reviewing proposed Python code for a software development workflow.

Task:
{task[:1000]}{target_info}{criteria_info}{plan_info}{test_info}

Complete proposed code:
{code}

Review instructions:
- Verify that the code satisfies the given task, implementation plan, and acceptance criteria.
- Ensure the code provides actual implementation logic rather than unrelated mock pipelines or placeholder classes.
{op_check}- Check syntax, logic, edge cases, security, and error handling.
- When reviewing HTTP/API calls, ensure numeric HTTP status codes (e.g. 404, 401, 403, 429) are checked rather than matching exception strings.
- Verify that the code is accompanied and verified by the associated test file and its unit test coverage.
- Identify concrete bugs; do not invent issues.
- Do not approve code merely because it looks well-written.
- Do not claim tests were executed.

Return REQUEST_CHANGES if any important requirement is missing or if the implementation is an unrelated mock.
If there are no concrete, important defects:
VERDICT: APPROVE
Issues: None
Fixes: None

If concrete, important defects exist:
VERDICT: REQUEST_CHANGES
Issues: [List up to 2 specific, verified defects]
Fixes: [Give a concrete fix for each defect]

Never include both verdicts in the same response.
"""

    def _has_verdict(self, content: str) -> bool:
        verdict = content.upper()
        return (
            "VERDICT: APPROVE" in verdict
            or "VERDICT: REQUEST_CHANGES" in verdict
        )
