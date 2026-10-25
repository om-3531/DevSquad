"""Reliable, display-ready unified diffs for dry-run workflow candidates."""

from __future__ import annotations

from difflib import unified_diff


def _display_lines(content: str) -> list[str]:
    """Split text while making an absent final newline visible in unified output."""
    return content.splitlines(keepends=True)


def _with_missing_newline_markers(lines: list[str]) -> str:
    rendered: list[str] = []
    for line in lines:
        rendered.append(line)
        if (
            line
            and line[0] in {"+", "-", " "}
            and not line.startswith(("+++", "---"))
            and not line.endswith("\n")
        ):
            rendered.append("\n\\ No newline at end of file\n")
    return "".join(rendered)


def build_diff_preview(
    *,
    path: str,
    operation: str,
    proposed_content: str,
    original_content: str | None,
    original_content_available: bool,
    unavailable_reason: str | None = None,
) -> dict:
    """Build a truthful preview without assuming missing source text is empty."""
    preview = {
        "path": path,
        "operation": operation,
        "original_content_available": original_content_available,
        "original_content_unavailable_reason": None,
        "diff": "",
        "changed": False,
        "reliable": original_content_available,
    }

    if not original_content_available:
        preview["original_content_unavailable_reason"] = (
            unavailable_reason or "The original file content is unavailable."
        )
        return preview

    original = original_content or ""
    if original == proposed_content:
        return preview

    fromfile = "/dev/null" if operation == "create" else f"a/{path}"
    tofile = f"b/{path}"
    raw_diff = list(
        unified_diff(
            _display_lines(original),
            _display_lines(proposed_content),
            fromfile=fromfile,
            tofile=tofile,
        )
    )
    preview["diff"] = _with_missing_newline_markers(raw_diff)
    preview["changed"] = bool(raw_diff)
    return preview
