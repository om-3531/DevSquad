
from fastapi import FastAPI, HTTPException

from backend.github.issues import IssueService
from backend.github.repository import RepositoryService
from backend.schemas import IssueRequest
from backend.agents.planner import PlannerAgent

app = FastAPI(
    title="DevSquad",
    description="AI-powered multi-agent software development system",
    version="0.1.0",
)


issue_service = IssueService()
repository_service = RepositoryService()
planner_agent = PlannerAgent()

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



@app.get("/github/repository")
async def get_github_repository(repo: str):
    try:
        repository_data = await repository_service.get_repository(
            repo=repo
        )

        return {
            "status": "success",
            "repository": repository_data,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )

@app.post("/agents/plan")
async def create_implementation_plan(request: IssueRequest):
    try:
        issue = await issue_service.get_issue(
            repo=request.repo,
            issue_number=request.issue_number,
        )

        plan = planner_agent.create_plan(
            title=issue.get("title", ""),
            description=issue.get("body") or "",
        )

        return {
            "status": "success",
            "issue_number": request.issue_number,
            "issue_title": issue.get("title"),
            "plan": plan,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )

@app.post("/agents/plan")
async def create_implementation_plan(request: IssueRequest):
    try:
        issue = await issue_service.get_issue(
            repo=request.repo,
            issue_number=request.issue_number,
        )

        file_data = await repository_service.get_file(
            repo=request.repo,
            path="README.md",
            branch="main",
        )

        repository_context = file_data.get(
            "decoded_content", ""
        )

        plan = planner_agent.create_plan(
            title=issue.get("title", ""),
            description=issue.get("body") or "",
            repository_context=repository_context,
        )

        return {
            "status": "success",
            "issue_number": request.issue_number,
            "issue_title": issue.get("title"),
            "context_file": "README.md",
            "plan": plan,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )
