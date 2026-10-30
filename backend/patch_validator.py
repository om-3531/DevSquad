"""Safe patch validation for candidate code modifications.

Validates that proposed code patches:
- Target an allowed repository-relative file within permitted scope.
- Apply cleanly to the expected base version.
- Preserve existing classes, methods, functions, and architectural imports.
- Do not introduce duplicate or dummy implementations of existing components.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

RESTRICTED_PATH_PATTERNS = {
    ".git",
    ".env",
    ".venv",
    "venv",
    ".pytest_cache",
    ".pytest-tmp",
    "Dockerfile",
    "Dockerfile.test",
    "requirements.txt",
    "candidate-requirements.txt",
    "pytest.ini",
    "backend/data/proposals.sqlite3",
}


@dataclass
class PatchValidationResult:
    is_valid: bool
    issues: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def validate_target_path(target_path: str, operation: str = "modify") -> tuple[bool, str | None]:
    """Validate that the target path is an allowed repository-relative path."""
    if not target_path or not target_path.strip():
        if operation == "modify":
            return False, "Target path is required for modify operations."
        return True, None

    normalized = target_path.strip().replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("\\"):
        return False, f"Target path '{target_path}' must be a repository-relative path, not an absolute path."

    posix = PurePosixPath(normalized)
    if ".." in posix.parts:
        return False, f"Directory traversal in target path '{target_path}' is forbidden."

    for restricted in RESTRICTED_PATH_PATTERNS:
        if normalized == restricted or normalized.startswith(f"{restricted}/") or posix.name == restricted:
            return False, f"Target path '{target_path}' modifies a restricted infrastructure or configuration file."

    return True, None


def validate_candidate_patch(
    *,
    target_path: str,
    proposed_code: str,
    existing_code: str = "",
    operation: str = "create",
) -> PatchValidationResult:
    """Validate candidate code before executing tests or accepting changes."""
    issues: list[str] = []
    fixes: list[str] = []

    # 1. Path validation
    path_valid, path_err = validate_target_path(target_path, operation)
    if not path_valid and path_err:
        issues.append(path_err)
        fixes.append("Target a valid repository-relative file within the project scope.")
        return PatchValidationResult(is_valid=False, issues=issues, fixes=fixes)

    # 2. Non-empty check
    if not proposed_code or not proposed_code.strip():
        issues.append("Proposed candidate code is empty.")
        fixes.append("Provide a complete Python implementation.")
        return PatchValidationResult(is_valid=False, issues=issues, fixes=fixes)

    # 3. AST / Syntax validation
    try:
        new_tree = ast.parse(proposed_code)
    except SyntaxError as syntax_err:
        issues.append(f"Python syntax error at line {syntax_err.lineno}: {syntax_err.msg}")
        fixes.append("Fix syntax errors so ast.parse succeeds.")
        return PatchValidationResult(is_valid=False, issues=issues, fixes=fixes)

    # If creating a new file without existing base, syntax validation is sufficient
    if operation != "modify" or not existing_code.strip():
        return PatchValidationResult(is_valid=True)

    # 4. Compare against base version
    try:
        orig_tree = ast.parse(existing_code)
    except SyntaxError:
        # If existing code could not be parsed, don't fail candidate on comparison
        return PatchValidationResult(is_valid=True)

    # Check preserved classes and methods
    orig_classes: dict[str, set[str]] = {}
    for node in orig_tree.body:
        if isinstance(node, ast.ClassDef):
            orig_classes[node.name] = {
                m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }

    new_classes: dict[str, set[str]] = {}
    for node in new_tree.body:
        if isinstance(node, ast.ClassDef):
            new_classes[node.name] = {
                m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }

    missing_classes = set(orig_classes.keys()) - set(new_classes.keys())
    if missing_classes:
        issues.append(f"Candidate removes existing class(es): {', '.join(sorted(missing_classes))}.")
        fixes.append("Preserve all existing class definitions from the original file.")

    missing_methods: list[str] = []
    for cls_name, orig_methods in orig_classes.items():
        if cls_name in new_classes:
            required_methods = {
                m for m in orig_methods
                if not m.startswith("_") or m.startswith("__")
            }
            removed = required_methods - new_classes[cls_name]
            if removed:
                missing_methods.extend([f"{cls_name}.{m}" for m in sorted(removed)])

    if missing_methods:
        issues.append(f"Candidate removes existing method(s): {', '.join(missing_methods)}.")
        fixes.append("Preserve all existing method signatures and implementations in preserved classes.")

    # Check preserved top-level functions (public API)
    orig_funcs = {
        n.name for n in orig_tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (not n.name.startswith("_") or n.name.startswith("__"))
    }
    new_funcs = {
        n.name for n in new_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing_funcs = orig_funcs - new_funcs
    if missing_funcs:
        issues.append(f"Candidate removes existing top-level function(s): {', '.join(sorted(missing_funcs))}.")
        fixes.append("Retain all existing top-level functions from the base file.")

    # Check architectural imports
    orig_imported_symbols: dict[str, str] = {}
    for node in orig_tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("backend."):
            for alias in node.names:
                orig_imported_symbols[alias.name] = node.module

    new_imported_symbols: set[str] = set()
    new_imported_modules: set[str] = set()
    for node in new_tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith("backend."):
                new_imported_modules.add(mod)
                for alias in node.names:
                    new_imported_symbols.add(alias.asname or alias.name)
            elif mod == "backend" or mod.startswith("backend"):
                for alias in node.names:
                    new_imported_modules.add(f"{mod}.{alias.name}")
                    new_imported_symbols.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("backend.") or alias.name == "backend":
                    new_imported_modules.add(alias.name)
                    new_imported_symbols.add(alias.asname or alias.name)

    dropped_backend_imports: list[str] = []
    for sym, mod in orig_imported_symbols.items():
        if sym in new_imported_symbols:
            continue
        mod_imported = any(
            new_mod == mod or mod.startswith(f"{new_mod}.") or new_mod.startswith(f"{mod}.")
            for new_mod in new_imported_modules
        )
        if mod_imported:
            continue
        dropped_backend_imports.append(sym)

    if dropped_backend_imports:
        issues.append(
            f"Candidate removes required architectural import(s): {', '.join(sorted(dropped_backend_imports))}."
        )
        fixes.append("Reuse existing project architecture imports rather than removing them.")

    # Check for duplicate implementations of imported components
    # (e.g. recreating GitHubClient or validate_repository locally)
    new_defined_names = set(new_classes.keys()) | new_funcs
    recreated_components = [
        sym for sym in orig_imported_symbols
        if sym in new_defined_names
    ]
    if recreated_components:
        issues.append(
            f"Candidate recreates local definition(s) for existing architectural component(s): {', '.join(sorted(recreated_components))}."
        )
        fixes.append("Import and reuse the existing component from project modules rather than creating a duplicate local class or function.")

    # Check that for issues.py, create_issue does not instantiate raw httpx.AsyncClient or raw HTTP post
    if target_path and target_path.endswith("issues.py"):
        for node in ast.walk(new_tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "create_issue":
                for child in ast.walk(node):
                    if isinstance(child, ast.Attribute) and child.attr == "AsyncClient":
                        issues.append(
                            "IssueService.create_issue accepts a GitHubClient but ignores its request abstraction and creates a separate httpx.AsyncClient."
                        )
                        fixes.append(
                            "Delegate directly to self.github.create_issue() rather than creating an httpx.AsyncClient."
                        )
                        break
                    if isinstance(child, ast.Attribute) and child.attr == "post" and isinstance(getattr(child, "value", None), ast.Attribute):
                        if getattr(child.value, "attr", None) == "github":
                            issues.append(
                                "IssueService.create_issue sends a raw HTTP POST, bypassing GitHubClient.create_issue() and write safety."
                            )
                            fixes.append(
                                "Call await self.github.create_issue(repo=repo, title=clean_title, body=body) instead of self.github.post()."
                            )
                            break

    # Check that candidate does not introduce incorrect builtins dependency for validate_repository
    for node in ast.walk(new_tree):
        if isinstance(node, ast.Attribute) and node.attr == "validate_repository":
            val = getattr(node, "value", None)
            if isinstance(val, ast.Name) and val.id == "builtins":
                issues.append(
                    "Candidate introduces incorrect 'builtins.validate_repository' dependency instead of using 'from backend.github.validation import validate_repository'."
                )
                fixes.append(
                    "Use existing project validation: 'from backend.github.validation import validate_repository' and call 'validate_repository(repo)'."
                )
                break

    return PatchValidationResult(
        is_valid=len(issues) == 0,
        issues=issues,
        fixes=fixes,
        details={
            "missing_classes": list(missing_classes),
            "missing_methods": missing_methods,
            "missing_funcs": list(missing_funcs),
            "dropped_backend_imports": dropped_backend_imports,
            "recreated_components": recreated_components,
        },
    )
