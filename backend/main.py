from fastapi import FastAPI, HTTPException

from backend.github.issues import IssueService
from backend.github.repository import RepositoryService
from backend.schemas import IssueRequest


app = FastAPI(
    title="DevSquad",
    description="AI-powered multi-agent software development system",
    version="0.1.0",
)


issue_service = IssueService()
repository_service = RepositoryService()


@app.get("/")
def root():
    return {
        "project": "DevSquad",
        "message": "DevSquad backend is running!",
        "version": "0.1.0",
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }


@app.post("/github/issue")
async def get_github_issue(request: IssueRequest):
    try:
        issue = await issue_service.get_issue(
            repo=request.repo,
            issue_number=request.issue_number,
        )

        return {
            "status": "success",
            "issue": issue,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@app.get("/github/file")
async def get_github_file(
    repo: str,
    path: str,
    branch: str = "main",
):
    try:
        file_data = await repository_service.get_file(
            repo=repo,
            path=path,
            branch=branch,
        )

        return {
            "status": "success",
            "file": file_data,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )
