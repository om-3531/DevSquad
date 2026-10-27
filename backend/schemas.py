
import re
from typing import Optional

from pydantic import BaseModel, field_validator


def is_safe_relative_path(path: str) -> bool:
    if not path or not isinstance(path, str):
        return False
    clean = path.strip()
    if (
        not clean
        or clean.startswith("/")
        or clean.startswith("\\")
        or "\\" in clean
        or any(part in ("", ".", "..") for part in clean.split("/"))
        or not clean.endswith(".py")
    ):
        return False
    return True


class IssueRequest(BaseModel):
    repo: str
    issue_number: int
    target_path: Optional[str] = None

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, value: str) -> str:
        clean = value.strip() if isinstance(value, str) else ""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", clean):
            raise ValueError("repo must use the owner/repository format.")
        return clean

    @field_validator("issue_number")
    @classmethod
    def validate_issue_number(cls, value: int) -> int:
        if value < 1:
            raise ValueError("issue_number must be greater than zero.")
        return value

    @field_validator("target_path")
    @classmethod
    def validate_target_path(cls, value):
        if value is None:
            return value
        if not is_safe_relative_path(value):
            raise ValueError(
                "target_path must be a safe relative Python file path."
            )
        return value.strip()


class WorkflowResponse(BaseModel):
    status: str
    message: str
    workflow_id: Optional[str] = None


class ProposalApprovalRequest(BaseModel):
    content_digest: str

    @field_validator("content_digest")
    @classmethod
    def validate_content_digest(cls, value: str) -> str:
        clean = value.strip().lower() if isinstance(value, str) else ""
        if not re.fullmatch(r"[0-9a-f]{64}", clean):
            raise ValueError("content_digest must be a SHA-256 digest.")
        return clean


class DiagnoseRequest(BaseModel):
    force_refresh: bool = False
