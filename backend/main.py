import uuid
import httpx

from backend.github.client import GitHubClient


from pathlib import Path

from fastapi import FastAPI, HTTPException

from backend.github.issues import IssueService
from backend.github.repository import RepositoryService
from backend.schemas import IssueRequest
from backend.agents.planner import PlannerAgent
from backend.agents.coder import CoderAgent
from backend.agents.tester import TesterAgent
from backend.agents.test_runner import TestRunner
from backend.agents.reviewer import ReviewerAgent
from backend.agents.generated_test_runner import GeneratedTestRunner


app = FastAPI(
    title="DevSquad",
    description="AI-powered multi-agent software development system",
    version="0.1.0",
)

issue_service = IssueService()
repository_service = RepositoryService()
planner_agent = PlannerAgent()
coder_agent = CoderAgent()
tester_agent = TesterAgent()
test_runner = TestRunner()
reviewer_agent = ReviewerAgent()
generated_test_runner = GeneratedTestRunner()
github_client = GitHubClient()

@app.get("/")
def root():
    return {
        "project": "DevSquad",
        "message": "DevSquad backend is running!",
        "version": "0.1.0",
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.post("/github/issue")
async def get_github_issue(request: IssueRequest):
    try:
        issue = await issue_service.get_issue(
            repo=request.repo,
            issue_number=request.issue_number,
        )
        return {"status": "success", "issue": issue}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/github/repository")
async def get_github_repository(repo: str):
    try:
        repository_data = await repository_service.get_repository(repo=repo)
        return {"status": "success", "repository": repository_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/agents/code")
async def generate_code(request: IssueRequest):
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
        repository_context = file_data.get("decoded_content", "")

        task = (
            f"Issue #{request.issue_number}: "
            f"{issue.get('title', '')}\\n"
            f"{issue.get('body') or ''}"
        )

        plan = planner_agent.create_plan(
            title=issue.get("title", ""),
            description=issue.get("body") or "",
            repository_context=repository_context,
        )
        proposed_code = coder_agent.generate_code(
            task=task,
            implementation_plan=str(plan),
            existing_code=repository_context,
        )
        test_suggestions = tester_agent.generate_tests(
            task=task,
            proposed_code=proposed_code,
        )

        return {
            "status": "success",
            "issue_number": request.issue_number,
            "issue_title": issue.get("title"),
            "plan": plan,
            "proposed_code": proposed_code,
            "test_suggestions": test_suggestions,
            "testing_status": "Test cases generated; not executed",
            "repository_modified": False,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/agents/review")
async def review_code(request: dict):
    task = request.get("task", "").strip()
    code = request.get("code", "").strip()

    if not task or not code:
        raise HTTPException(
            status_code=400,
            detail="Both task and code are required.",
        )

    try:
        review = reviewer_agent.review_code(task=task, code=code)
        return {"status": "success", "review": review}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Review failed: {type(e).__name__}: {e}",
        ) from e


@app.post("/agents/test")
async def run_agent_tests(test_file: str):
    try:
        result = test_runner.run_tests(test_file)
        return {
            "status": "success",
            "test_file": test_file,
            "passed": result["passed"],
            "return_code": result["return_code"],
            "summary": result.get("summary", "Test summary unavailable"),
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


        
@app.post("/agents/workflow")
async def run_agent_workflow(request: IssueRequest):
    try:
        issue = await issue_service.get_issue(
            repo=request.repo,
            issue_number=request.issue_number,
        )

        task = (
            f"Issue #{request.issue_number}: "
            f"{issue.get('title', '')}\n"
            f"{issue.get('body') or ''}"
        )

        file_data = await repository_service.get_file(
            repo=request.repo,
            path="README.md",
            branch="main",
        )
        repository_context = file_data.get("decoded_content", "")

        plan = planner_agent.create_plan(
            title=issue.get("title", ""),
            description=issue.get("body") or "",
            repository_context=repository_context,
        )

        proposed_code = coder_agent.generate_code(
            task=task,
            implementation_plan=str(plan),
            existing_code=repository_context,
        )

        test_suggestions = tester_agent.generate_tests(
            task=task,
            proposed_code=proposed_code,
        )

        max_retries = 2
        correction_attempts = 0
        review = ""
        quality_status = "NEEDS_MANUAL_REVIEW"

        for attempt in range(max_retries + 1):
            review = reviewer_agent.review_code(
                task=task,
                code=proposed_code,
            )
            verdict = review.upper()

            if "VERDICT: REQUEST_CHANGES" in verdict:
                quality_status = "REQUEST_CHANGES"
                if attempt < max_retries:
                    proposed_code = coder_agent.improve_code(
                        task=task,
                        implementation_plan=str(plan),
                        previous_code=proposed_code,
                        reviewer_feedback=review,
                    )
                    correction_attempts += 1
                    test_suggestions = tester_agent.generate_tests(
                        task=task,
                        proposed_code=proposed_code,
                    )
                    continue
                break

            if "VERDICT: APPROVE" in verdict:
                quality_status = "APPROVED"
            break

        test_execution = {
            "status": "NOT_RUN",
            "passed": False,
            "summary": "Tests were not executed.",
            "return_code": None,
        }

        if quality_status == "APPROVED":
            test_execution = generated_test_runner.run_generated_tests(
                proposed_code=proposed_code,
                generated_tests=test_suggestions,
            )
            quality_status = (
                "REVIEW_APPROVED_GENERATED_TESTS_PASSED"
                if test_execution["passed"]
                else "GENERATED_TESTS_FAILED"
            )

        repository_modified = False
        pull_request = None
        commit_result = None
        branch_name = None
        next_action = "Manual review required."

        if quality_status == "REVIEW_APPROVED_GENERATED_TESTS_PASSED":
            next_action = (
                "Review and generated tests passed. "
                "No repository changes were made."
            )

            if request.target_path:
                target_path = request.target_path

                # Only create new Python files in this initial workflow.
                if not target_path.endswith(".py"):
                    raise HTTPException(
                        status_code=400,
                        detail="Only new .py files are supported.",
                    )

                try:
                    await repository_service.get_file(
                        repo=request.repo,
                        path=target_path,
                        branch="main",
                    )
                except httpx.HTTPStatusError as error:
                    if error.response.status_code != 404:
                        raise
                else:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Target file already exists. "
                            "Overwriting existing files is not supported."
                        ),
                    )

                branch_name = (
                    f"devsquad/issue-{request.issue_number}-"
                    f"{uuid.uuid4().hex[:8]}"
                )

                await github_client.create_branch(
                    repo=request.repo,
                    branch_name=branch_name,
                    source_branch="main",
                )

                commit_result = await github_client.commit_files(
                    repo=request.repo,
                    branch=branch_name,
                    files={target_path: proposed_code},
                    message=f"Implement issue #{request.issue_number}",
                )

                pull_request = await github_client.create_pull_request(
                    repo=request.repo,
                    title=f"Draft: {issue.get('title', '')}",
                    head=branch_name,
                    base="main",
                    body=(
                        f"Closes #{request.issue_number}\n\n"
                        "Generated by DevSquad. Please review the code "
                        "and test results before merging."
                    ),
                    draft=True,
                )

                repository_modified = True
                next_action = "Draft pull request created for manual review."

        return {
            "status": "success",
            "issue_number": request.issue_number,
            "issue_title": issue.get("title"),
            "plan": plan,
            "proposed_code": proposed_code,
            "test_suggestions": test_suggestions,
            "review": review,
            "quality_gate": {
                "status": quality_status,
                "approved": (
                    quality_status
                    == "REVIEW_APPROVED_GENERATED_TESTS_PASSED"
                ),
                "next_action": next_action,
            },
            "correction_attempts": correction_attempts,
            "max_correction_attempts": max_retries,
            "testing_status": test_execution["status"],
            "test_execution": test_execution,
            "repository_modified": repository_modified,
            "branch": branch_name,
            "commit": commit_result,
            "pull_request": pull_request,
        }

    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Workflow failed: {type(error).__name__}: {error}",
        ) from error


