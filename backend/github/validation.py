"""Strict validation for values interpolated into GitHub API paths."""

from __future__ import annotations

import re


_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}", re.IGNORECASE)


def validate_repository(value: str) -> str:
    if not isinstance(value, str) or not _REPOSITORY.fullmatch(value):
        raise ValueError("Invalid GitHub repository identifier (Invalid repo).")
    return value


def validate_git_ref(value: str) -> str:
    if (
        not isinstance(value, str)
        or not _REF.fullmatch(value)
        or ".." in value
        or "//" in value
        or "@{" in value
        or value in {"HEAD", "@"}
        or value.endswith(("/", ".", ".lock"))
    ):
        raise ValueError("Invalid Git ref.")
    return value


def validate_file_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith(("/", "\\"))
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("Invalid repository file path.")
    return value


def validate_sha(value: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError("Invalid Git object SHA.")
    return value.lower()
