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


@app.post("/agents/workflow")
async def run_agent_workflow(request: IssueRequest):
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

        max_retries = 2
        correction_attempts = 0
        review = ""
        quality_status = "NEEDS_MANUAL_REVIEW"
        next_action = "Manual review required."

        for attempt in range(max_retries + 1):
            review = reviewer_agent.review_code(
                task=task,
                code=proposed_code,
            )
            review_text = review.upper()

            if "VERDICT: REQUEST_CHANGES" in review_text:
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

                next_action = (
                    "Maximum correction attempts reached. "
                    "Manual review required."
                )
                break

            if "VERDICT: APPROVE" in review_text:
                quality_status = "APPROVED"
                next_action = "Code is approved for the testing stage."
                break

            quality_status = "NEEDS_MANUAL_REVIEW"
            next_action = (
                "Reviewer verdict was unclear. Manual review required."
            )
            break

        # This runs a trusted, existing project test file, not the
        # AI-generated proposal. Passing it does not prove the proposal works.
        test_execution = {
            "status": "NOT_RUN",
            "passed": False,
            "summary": "Tests were not executed.",
            "return_code": None,
        }

        if quality_status == "APPROVED":
            test_result = test_runner.run_tests("test_runner_check.py")
            test_execution = {
                "status": (
                    "TESTS_PASSED"
                    if test_result["passed"]
                    else "TESTS_FAILED"
                ),
                "passed": test_result["passed"],
                "summary": test_result.get(
                    "summary", "Test summary unavailable"
                ),
                "return_code": test_result["return_code"],
            }

            if test_result["passed"]:
                quality_status = (
                    "REVIEW_APPROVED_EXISTING_TESTS_PASSED"
                )
                next_action = (
                    "Reviewer approved the proposal and the selected "
                    "existing project test file passed. The AI-generated "
                    "proposal itself was not executed; manual integration "
                    "checks are still required."
                )
            else:
                quality_status = "TESTS_FAILED"
                next_action = (
                    "The selected project test file failed. Inspect "
                    "the test output before proceeding."
                )
        elif quality_status == "REQUEST_CHANGES":
            next_action = (
                "Maximum correction attempts reached or changes "
                "are still required. Tests were not executed."
            )
        else:
            next_action = "Code was not approved. Tests were not executed."

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
                    == "REVIEW_APPROVED_EXISTING_TESTS_PASSED"
                ),
                "next_action": next_action,
            },
            "correction_attempts": correction_attempts,
            "max_correction_attempts": max_retries,
            "testing_status": test_execution["status"],
            "test_execution": test_execution,
            "repository_modified": False,
            "quality_status": quality_status,
            "next_action": next_action,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Workflow failed: {type(e).__name__}: {e}",
        ) from e


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
