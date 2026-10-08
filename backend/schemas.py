from pydantic import BaseModel
from typing import Optional


class IssueRequest(BaseModel):
    repo: str
    issue_number: int


class WorkflowResponse(BaseModel):
    status: str
    message: str
    workflow_id: Optional[str] = None
