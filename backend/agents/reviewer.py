import ast
import os
import time

from groq import Groq, RateLimitError
from backend.config import settings
from backend.agents.llm_text import (
    execute_chat_completion,
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

    def __init__(self, fallback_model: str | None = None):
        self._model = None
        self._fallback_model = fallback_model or settings.LLM_FALLBACK_MODEL
        self.on_fallback = None
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

    @property
    def fallback_model(self) -> str | None:
        return getattr(self, "_fallback_model", None)

    @fallback_model.setter
    def fallback_model(self, value: str | None):
        self._fallback_model = value

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
                # Check for client bypass in issues.py first
                if target_path and target_path.endswith("issues.py"):
                    for node in ast.walk(new_tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "create_issue":
                            for child in ast.walk(node):
                                if isinstance(child, ast.Attribute) and child.attr == "AsyncClient":
                                    return (
                                        "VERDICT: REQUEST_CHANGES\n"
                                        "Issues:\n"
                                        "1. IssueService.create_issue accepts a GitHubClient but ignores its request abstraction and creates a separate httpx.AsyncClient.\n"
                                        "2. The implementation sends a raw HTTP POST directly to the GitHub API, bypassing GitHubClient.create_issue() and its GITHUB_WRITES_ENABLED safety guard.\n"
                                        "Fixes:\n"
                                        "1. Delegate directly to self.github.create_issue(repo=repo, title=clean_title, body=body) rather than creating another httpx.AsyncClient.\n"
                                        "2. Preserve the create_test_issue wrapper and pass the injected client through to IssueService."
                                    )
                                if isinstance(child, ast.Attribute) and child.attr == "post" and isinstance(getattr(child, "value", None), ast.Attribute):
                                    if getattr(child.value, "attr", None) == "github":
                                        return (
                                            "VERDICT: REQUEST_CHANGES\n"
                                            "Issues:\n"
                                            "1. IssueService.create_issue sends a raw HTTP POST via self.github.post rather than using the real supported GitHubClient.create_issue() API.\n"
                                            "2. This bypasses the GitHubClient.create_issue() method and its GITHUB_WRITES_ENABLED write safety guard.\n"
                                            "Fixes:\n"
                                            "1. Call await self.github.create_issue(repo=repo, title=clean_title, body=body) directly.\n"
                                            "2. Ensure create_test_issue passes the supplied client through to IssueService."
                                        )

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
                        required_methods = {
                            m for m in orig_methods
                            if not m.startswith("_") or m.startswith("__")
                        }
                        diff = required_methods - new_classes[cls_name]
                        if diff:
                            missing_methods.extend([f"{cls_name}.{m}" for m in sorted(diff)])

                # Check for dropped architectural imports and duplicate local implementations
                orig_imports: dict[str, str] = {}
                for n in orig_tree.body:
                    if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("backend."):
                        for alias in n.names:
                            orig_imports[alias.name] = n.module

                new_imports: set[str] = set()
                new_imported_modules: set[str] = set()
                for n in new_tree.body:
                    if isinstance(n, ast.ImportFrom):
                        mod = n.module or ""
                        if mod.startswith("backend."):
                            new_imported_modules.add(mod)
                            for alias in n.names:
                                new_imports.add(alias.asname or alias.name)
                        elif mod == "backend" or mod.startswith("backend"):
                            for alias in n.names:
                                new_imported_modules.add(f"{mod}.{alias.name}")
                                new_imports.add(alias.asname or alias.name)
                    elif isinstance(n, ast.Import):
                        for alias in n.names:
                            if alias.name.startswith("backend.") or alias.name == "backend":
                                new_imported_modules.add(alias.name)
                                new_imports.add(alias.asname or alias.name)

                dropped_arch_imports = []
                for sym, mod in orig_imports.items():
                    if sym in new_imports:
                        continue
                    mod_imported = any(
                        new_mod == mod or mod.startswith(f"{new_mod}.") or new_mod.startswith(f"{mod}.")
                        for new_mod in new_imported_modules
                    )
                    if mod_imported:
                        continue
                    dropped_arch_imports.append(sym)

                recreated_components = [
                    sym for sym in orig_imports
                    if sym in new_classes or sym in {n.name for n in new_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                ]

                if missing_classes or missing_methods or dropped_arch_imports or recreated_components:
                    missing_desc = []
                    if missing_classes:
                        missing_desc.append(f"classes: {', '.join(sorted(missing_classes))}")
                    if missing_methods:
                        missing_desc.append(f"methods: {', '.join(missing_methods)}")
                    if dropped_arch_imports:
                        missing_desc.append(f"dropped imports: {', '.join(sorted(dropped_arch_imports))}")
                    if recreated_components:
                        missing_desc.append(f"duplicate local implementations of imported components: {', '.join(sorted(recreated_components))}")
                    return (
                        "VERDICT: REQUEST_CHANGES\n"
                        f"Issues:\n1. Architectural preservation violation ({'; '.join(missing_desc)}). Original codebase and architecture must be preserved.\n"
                        "Fixes:\n1. Retain all original classes, methods, and architectural imports. Reuse existing components instead of creating duplicate local classes or functions."
                    )
            except Exception:
                pass

        last_error = "Reviewer returned an empty response."
        max_attempts = 2
        extra_kwargs = get_model_extra_kwargs(self.model)

        def _on_switch(prev, next_m, reason):
            self.model = next_m
            if getattr(self, "on_fallback", None):
                self.on_fallback(prev, next_m, reason)

        for attempt in range(max_attempts):
            try:
                response, used_model = execute_chat_completion(
                    client=self.client,
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
                    primary_model=self.model,
                    fallback_model=self.fallback_model,
                    temperature=0.1,
                    max_tokens=700,
                    extra_kwargs=extra_kwargs,
                    agent_name="Reviewer",
                    max_retries_per_model=2,
                    base_backoff=2.0,
                    max_backoff=10.0,
                    on_model_switch=_on_switch,
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
- Focus review strictly on the specific target file ({target_path or 'target module'}). Do not request changes for external files, web routing endpoints, or test suites that belong outside this module.
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
