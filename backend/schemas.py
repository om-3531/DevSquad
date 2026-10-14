
from typing import Optional

from pydantic import BaseModel, field_validator


class IssueRequest(BaseModel):
    repo: str
    issue_number: int
    target_path: Optional[str] = None

    @field_validator("target_path")
    @classmethod
    def validate_target_path(cls, value):
        if value is None:
            return value

        path = value.strip()

        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or any(part in ("", ".", "..") for part in path.split("/"))
            or not path.endswith(".py")
        ):
            raise ValueError(
                "target_path must be a safe relative Python file path."
            )

        return path


class WorkflowResponse(BaseModel):
    status: str
    message: str
    workflow_id: Optional[str] = None
