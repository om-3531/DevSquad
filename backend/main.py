import logging
import os
import re
from contextlib import asynccontextmanager
import httpx
from groq import RateLimitError

from backend.config import settings
from backend.github.client import GitHubClient

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.github.issues import IssueService
from backend.github.repository import RepositoryService
from backend.github.write_orchestrator import ProposalWriteOrchestrator
from backend.github.client import GitHubStaleRevisionError, GitHubWritesDisabledError
from backend.schemas import (
    IssueRequest,
    ProposalApprovalRequest,
    is_safe_relative_path,
)
from backend.diff_preview import build_diff_preview
from backend.proposals import (
    ProposalStorageError,
    ProposalStore,
    StaleProposalError,
)
from backend.auth import (
    ApprovalRateLimiter,
    AuthenticationConfigurationError,
    AuthenticationError,
    AuthorizationError,
    AuthenticatedIdentity,
    BearerTokenAuthenticator,
    PROPOSAL_APPROVER,
    PROPOSAL_READER,
)
from backend.agents.planner import PlannerAgent
from backend.agents.coder import CoderAgent
from backend.agents.tester import TesterAgent
from backend.agents.test_runner import TestRunner
from backend.agents.reviewer import ReviewerAgent
from backend.agents.generated_test_runner import GeneratedTestRunner
from backend.agents.llm_text import (
    extract_model_from_error,
    extract_retry_after,
    format_rate_limit_detail,
    is_rate_limit_error,
)
from backend.agents.model_manager import (
    check_model_availability,
    get_available_models,
    get_model_limits,
    recommend_fallback_model,
    sanitize_secret,
)

logger = logging.getLogger("backend.main")


def log_startup_configuration():
    """Log startup configuration cleanly without leaking secrets or tokens."""
    primary_model = settings.get_model()
    fallback_model = settings.LLM_FALLBACK_MODEL
    logger.info("DevSquad backend starting up.")
    logger.info("Primary LLM model: %s", primary_model)
    logger.info("Fallback LLM model: %s", fallback_model)
    role_models = {
        "planner": settings.get_model("planner"),
        "coder": settings.get_model("coder"),
        "tester": settings.get_model("tester"),
        "reviewer": settings.get_model("reviewer"),
    }
    for role, role_model in role_models.items():
        if role_model != primary_model:
            logger.info("Role '%s' model: %s", role, role_model)
    logger.info("GitHub writes enabled: %s", settings.GITHUB_WRITES_ENABLED)
    if settings.GITHUB_REPO:
        logger.info("Configured repository: %s", settings.GITHUB_REPO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_startup_configuration()
    yield


app = FastAPI(
    title="DevSquad",
    description="AI-powered multi-agent software development system",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
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
proposal_store = ProposalStore(settings.PROPOSAL_DB_PATH)
proposal_write_orchestrator = ProposalWriteOrchestrator(proposal_store, repository_service, github_client)
approval_authenticator = BearerTokenAuthenticator.from_json(settings.APPROVAL_AUTH_TOKENS)
approval_rate_limiter = ApprovalRateLimiter()


def get_proposal_write_orchestrator() -> ProposalWriteOrchestrator:
    """Return an orchestrator bound to the active proposal store and services."""
    return ProposalWriteOrchestrator(proposal_store, repository_service, github_client)



def get_approval_authenticator() -> BearerTokenAuthenticator:
    """Return an authenticator initialized with the active token configuration."""
    raw_tokens = os.getenv("APPROVAL_AUTH_TOKENS") or settings.APPROVAL_AUTH_TOKENS
    return BearerTokenAuthenticator.from_json(raw_tokens)


def internal_error_detail() -> str:
    """Return a safe client-facing message for unexpected server failures."""
    return "Internal server error. Check server logs for diagnostic details."


def require_proposal_role(role: str):
    async def dependency(authorization: str | None = Header(default=None)) -> AuthenticatedIdentity:
        try:
            authenticator = get_approval_authenticator()
            identity = authenticator.authenticate(authorization)
            return authenticator.require_role(identity, role)
        except AuthenticationConfigurationError as error:
            raise HTTPException(status_code=503, detail="Proposal authentication is not configured.") from error
        except AuthenticationError as error:
            raise HTTPException(status_code=401, detail="Authentication credentials are required or invalid.", headers={"WWW-Authenticate": "Bearer"}) from error
        except AuthorizationError as error:
            raise HTTPException(status_code=403, detail="You are not authorized to perform this action.") from error

    return dependency



def select_relevant_files(
    tree_paths: list[str],
    issue_title: str,
    issue_body: str,
    target_path: str = "",
    max_files: int = 12,
) -> list[str]:
    """
    Filter repository file tree to only Python files and paths relevant to the issue.
    Avoids passing dozens of irrelevant repository files to LLM prompts.
    """
    if not tree_paths:
        return []

    ignored_extensions = (
        ".md", ".txt", ".rst", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".lock", ".svg", ".png",
        ".jpg", ".ico", ".html", ".css", ".map",
    )

    candidates = [
        p for p in tree_paths
        if p.endswith(".py") or (target_path and p == target_path)
    ]
    if not candidates:
        candidates = [p for p in tree_paths if not p.endswith(ignored_extensions)]

    text_content = f"{issue_title} {issue_body} {target_path}".lower()
    words = set(re.findall(r"[a-z0-9_]{3,}", text_content))

    def score_path(path: str) -> int:
        score = 0
        p_lower = path.lower()
        if target_path and path == target_path:
            score += 100
        for w in words:
            if w in p_lower:
                score += 5
        if "test" not in text_content and ("test_" in p_lower or "_test" in p_lower):
            score -= 2
        return score

    scored = sorted(candidates, key=score_path, reverse=True)
    return scored[:max_files]


def handle_ai_rate_limit(error: Exception, model: str | None = None) -> HTTPException:
    """Build a user-friendly HTTPException (429) containing exact retry seconds and fallback advice."""
    active_model = model or extract_model_from_error(error) or settings.get_model()
    retry_after = extract_retry_after(error)
    client = getattr(planner_agent, "client", None)
    fallback = recommend_fallback_model(client, active_model, settings.LLM_FALLBACK_MODEL)
    info = format_rate_limit_detail(error, active_model, retry_after, fallback)

    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(int(max(retry_after, 1.0)))

    return HTTPException(
        status_code=429,
        detail=info["detail"],
        headers=headers,
    )


def github_http_error(error: httpx.HTTPStatusError) -> HTTPException:
    status = error.response.status_code
    messages = {
        401: "GitHub authentication failed. Check GITHUB_TOKEN.",
        403: "GitHub permission denied or API rate limit exceeded.",
        404: "GitHub repository, issue, or file not found.",
        409: "GitHub reported a conflict.",
        422: "GitHub rejected the request.",
        429: "GitHub API rate limit exceeded. Please try again later.",
    }
    return HTTPException(
        status_code=status,
        detail=messages.get(status, f"GitHub request failed with HTTP {status}."),
    )




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


@app.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str, _: AuthenticatedIdentity = Depends(require_proposal_role(PROPOSAL_READER))):
    try:
        proposal = proposal_store.get(proposal_id)
    except ProposalStorageError as error:
        raise HTTPException(status_code=503, detail="Proposal storage is temporarily unavailable.") from error
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found.")
    return {"status": "success", "proposal": proposal.public_dict()}


@app.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, request: ProposalApprovalRequest, identity: AuthenticatedIdentity = Depends(require_proposal_role(PROPOSAL_APPROVER))):
    retry_after = approval_rate_limiter.check(identity.subject)
    if retry_after is not None:
        raise HTTPException(status_code=429, detail="Too many approval attempts. Please try again later.", headers={"Retry-After": str(retry_after)})
    try:
        proposal = proposal_store.approve(proposal_id, request.content_digest, approved_by=identity.subject)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Proposal not found.") from error
    except TimeoutError as error:
        proposal_store.record_audit(proposal_id, identity.subject, "REJECTED_EXPIRED", request.content_digest)
        raise HTTPException(status_code=410, detail="Proposal expired; run a new dry-run workflow.") from error
    except FileExistsError as error:
        proposal_store.record_audit(proposal_id, identity.subject, "REJECTED_ALREADY_APPROVED", request.content_digest)
        raise HTTPException(status_code=409, detail="Proposal has already been approved.") from error
    except PermissionError as error:
        proposal_store.record_audit(proposal_id, identity.subject, "REJECTED_DIGEST_MISMATCH", request.content_digest)
        raise HTTPException(status_code=409, detail="Proposal content does not match the reviewed candidate.") from error
    except ValueError as error:
        proposal_store.record_audit(proposal_id, identity.subject, "REJECTED_NOT_ELIGIBLE", request.content_digest)
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ProposalStorageError as error:
        raise HTTPException(status_code=503, detail="Proposal storage is temporarily unavailable.") from error

    return {
        "status": "success",
        "message": "Approval recorded. No branch, commit, or pull request was created.",
        "proposal": proposal.public_dict(),
    }


@app.post("/proposals/{proposal_id}/write")
async def write_proposal(proposal_id: str, identity: AuthenticatedIdentity = Depends(require_proposal_role(PROPOSAL_APPROVER))):
    if not settings.GITHUB_WRITES_ENABLED:
        raise HTTPException(status_code=503, detail="GitHub writes are disabled.")
    retry_after = approval_rate_limiter.check(identity.subject)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many write attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        orchestrator = get_proposal_write_orchestrator()
        result = await orchestrator.execute(proposal_id, identity.subject)
        return {
            "status": "success",
            "message": "Approved proposal committed and draft pull request created.",
            "proposal": result["proposal"].public_dict(),
            "commit": result["commit"],
            "pull_request": result["pull_request"],
        }
    except GitHubWritesDisabledError as error:
        raise HTTPException(status_code=503, detail="GitHub writes are disabled.") from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Proposal not found.") from error
    except (StaleProposalError, GitHubStaleRevisionError) as error:
        raise HTTPException(status_code=409, detail="Proposal is stale; run a new dry-run workflow.") from error
    except (ValueError, PermissionError, TimeoutError, FileExistsError) as error:
        raise HTTPException(status_code=409, detail="Proposal is not eligible for writing.") from error
    except httpx.HTTPStatusError as error:
        raise github_http_error(error) from error
    except ProposalStorageError as error:
        raise HTTPException(status_code=503, detail="Proposal storage is temporarily unavailable.") from error
    except Exception as error:
        raise HTTPException(status_code=502, detail="GitHub write workflow failed safely.") from error


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
    except httpx.HTTPStatusError as error:
        raise github_http_error(error) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=internal_error_detail()) from error


@app.get("/github/repository")
async def get_github_repository(
    repo: str = Query(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"),
):
    try:
        repository_data = await repository_service.get_repository(repo=repo)
        return {"status": "success", "repository": repository_data}
    except HTTPException:
        raise
    except httpx.HTTPStatusError as error:
        raise github_http_error(error) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=internal_error_detail()) from error


@app.get("/models/status")
async def get_models_status():
    """Check currently active LLM model, availability from Groq, and recommended fallback."""
    current_model = settings.get_model()
    client = getattr(planner_agent, "client", None)
    available = get_available_models(client)
    current_available = current_model in available if available else True
    limits = get_model_limits(current_model)
    fallback = recommend_fallback_model(client, current_model, settings.LLM_FALLBACK_MODEL)
    return {
        "status": "success",
        "current_model": current_model,
        "is_available": current_available,
        "configured_fallback": settings.LLM_FALLBACK_MODEL,
        "recommended_fallback": fallback,
        "available_models": available,
        "limits": limits,
    }


@app.post("/agents/code")
async def generate_code(request: IssueRequest):
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

        target_existing_code = ""
        if request.target_path:
            try:
                target_file_data = await repository_service.get_file(
                    repo=request.repo,
                    path=request.target_path,
                    branch="main",
                )
                target_existing_code = target_file_data.get("decoded_content", "")
            except httpx.HTTPStatusError:
                target_existing_code = ""

        plan = planner_agent.create_plan(
            title=issue.get("title", ""),
            description=issue.get("body") or "",
            repository_context=target_existing_code[:2000] if target_existing_code else "",
            target_path=request.target_path or "",
        )

        proposed_code = coder_agent.generate_code(
            task=task,
            implementation_plan=str(plan),
            existing_code=target_existing_code,
            file_path=request.target_path or "",
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
    except Exception as error:
        if is_rate_limit_error(error):
            raise handle_ai_rate_limit(error, model=settings.get_model("coder")) from error
        raise HTTPException(status_code=500, detail=internal_error_detail()) from error


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
    except Exception as error:
        if is_rate_limit_error(error):
            raise handle_ai_rate_limit(error, model=settings.get_model("reviewer")) from error
        raise HTTPException(
            status_code=500,
            detail="Review failed. Check server logs for diagnostic details.",
        ) from error



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
    except Exception as error:
        raise HTTPException(status_code=500, detail=internal_error_detail()) from error


        
@app.post("/agents/workflow")
async def run_agent_workflow(request: IssueRequest):
    try:
        issue = await issue_service.get_issue(
            repo=request.repo,
            issue_number=request.issue_number,
        )
        issue_title = issue.get("title", "")
        issue_body = issue.get("body") or ""

        task = (
            f"Issue #{request.issue_number}: {issue_title}\n"
            f"{issue_body}"
        )

        repository_data = await repository_service.get_repository(
            repo=request.repo,
        )
        source_branch = repository_data.get("default_branch") or "main"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", source_branch):
            raise HTTPException(status_code=502, detail="Repository returned an invalid default branch.")
        base_commit_sha = await repository_service.get_branch_sha(
            request.repo,
            source_branch,
        )

        repo_files = await repository_service.get_tree(
            repo=request.repo,
            branch=source_branch,
        )

        relevant_repo_files = select_relevant_files(
            tree_paths=repo_files,
            issue_title=issue_title,
            issue_body=issue_body,
            target_path=request.target_path or "",
        )

        # Do not send irrelevant files or entire README unless explicitly targeted
        repository_context = ""
        if request.target_path and request.target_path in repo_files:
            try:
                target_data = await repository_service.get_file(
                    repo=request.repo,
                    path=request.target_path,
                    branch=source_branch,
                )
                repository_context = target_data.get("decoded_content", "")[:2500]
            except Exception:
                repository_context = ""

        plan = planner_agent.create_plan(
            title=issue_title,
            description=issue_body,
            repository_context=repository_context,
            repo_files=relevant_repo_files,
            target_path=request.target_path or "",
        )

        acceptance_criteria = (
            plan.get("acceptance_criteria", [])
            if isinstance(plan, dict)
            else []
        )
        target_files_plan = (
            plan.get("target_files", [])
            if isinstance(plan, dict)
            else []
        )

        target_path = None
        if request.target_path:
            if not is_safe_relative_path(request.target_path):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid target path: {request.target_path!r}. Must be a safe relative Python file path.",
                )
            target_path = request.target_path
        elif (
            target_files_plan
            and isinstance(target_files_plan, list)
            and len(target_files_plan) > 0
            and isinstance(target_files_plan[0], dict)
            and target_files_plan[0].get("path")
        ):
            candidate = target_files_plan[0]["path"].strip()
            if is_safe_relative_path(candidate):
                target_path = candidate

        existing_code = ""
        original_content: str | None = None
        original_content_available = False
        original_content_unavailable_reason = None
        target_file_blob_sha: str | None = None
        operation = "create"
        if target_path:
            try:
                file_data = await repository_service.get_file(
                    repo=request.repo,
                    path=target_path,
                    branch=source_branch,
                )
                operation = "modify"
                decoded_content = file_data.get("decoded_content")
                if isinstance(decoded_content, str):
                    file_sha = file_data.get("sha")
                    if isinstance(file_sha, str):
                        target_file_blob_sha = file_sha
                    existing_code = decoded_content
                    original_content = decoded_content
                    original_content_available = True
                else:
                    original_content_unavailable_reason = (
                        "The target file could not be retrieved as UTF-8 text."
                    )
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 404:
                    raise
                existing_code = ""
                original_content = ""
                original_content_available = True
                operation = "create"

        proposed_code = coder_agent.generate_code(
            task=task,
            implementation_plan=str(plan),
            existing_code=existing_code,
            operation=operation,
            file_path=target_path or "",
            acceptance_criteria=acceptance_criteria,
        )

        test_suggestions = ""
        max_retries = 2
        correction_attempts = 0
        review = ""
        quality_status = "NEEDS_MANUAL_REVIEW"
        test_execution = {
            "status": "NOT_RUN",
            "passed": False,
            "summary": "Tests were not executed.",
            "return_code": None,
        }

        for attempt in range(max_retries + 1):
            review = reviewer_agent.review_code(
                task=task,
                code=proposed_code,
                plan=str(plan),
                target_path=target_path or "",
                existing_code=existing_code,
                operation=operation,
                acceptance_criteria=acceptance_criteria,
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
                        existing_code=existing_code,
                        operation=operation,
                        file_path=target_path or "",
                        acceptance_criteria=acceptance_criteria,
                    )
                    correction_attempts += 1
                    continue
                break

            if "VERDICT: APPROVE" not in verdict:
                quality_status = "NEEDS_MANUAL_REVIEW"
                break

            if not test_suggestions:
                test_suggestions = tester_agent.generate_tests(
                    task=task,
                    proposed_code=proposed_code,
                )

            test_execution = generated_test_runner.run_generated_tests(
                proposed_code=proposed_code,
                generated_tests=test_suggestions,
            )
            if test_execution["passed"]:
                quality_status = "REVIEW_APPROVED_GENERATED_TESTS_PASSED"
                break

            if test_execution.get("status") == "DOCKER_ERROR":
                quality_status = "TEST_ENVIRONMENT_ERROR"
                break

            quality_status = "GENERATED_TESTS_FAILED"
            if attempt < max_retries:
                proposed_code = coder_agent.improve_code(
                    task=task,
                    implementation_plan=str(plan),
                    previous_code=proposed_code,
                    reviewer_feedback=(
                        "Generated tests failed. Fix the proposed code "
                        "so the isolated pytest suite passes.\n"
                        f"{test_execution.get('summary', '')}"
                    ),
                    existing_code=existing_code,
                    operation=operation,
                    file_path=target_path or "",
                    acceptance_criteria=acceptance_criteria,
                )
                correction_attempts += 1
                continue
            break

        # This endpoint deliberately never mutates GitHub.  A successful local
        # workflow is a candidate for a later, explicitly authorized write
        # operation; it is not authorization to create a branch or pull request.
        repository_modified = False
        pull_request = None
        commit_result = None
        branch_name = None
        write_simulation = None
        diff_preview = None
        if target_path:
            diff_preview = build_diff_preview(
                path=target_path,
                operation=operation,
                proposed_content=proposed_code,
                original_content=original_content,
                original_content_available=original_content_available,
                unavailable_reason=original_content_unavailable_reason,
            )
        diff_previews = [diff_preview] if diff_preview else []
        files_changed = (
            [{"path": target_path, "operation": operation}]
            if diff_preview and diff_preview["changed"]
            else []
        )

        if quality_status == "REVIEW_APPROVED_GENERATED_TESTS_PASSED":
            next_action = (
                "Review and generated tests passed. "
                "No repository changes were made."
            )

            if target_path:
                write_simulation = {
                    "mode": "dry_run",
                    "performed": False,
                    "reason": "GitHub writes are disabled pending explicit user authorization.",
                    "would_create": ["feature branch", "commit", "draft pull request"],
                    "base_branch": source_branch,
                    "commit_message": f"fix(#{request.issue_number}): {issue_title}",
                }
                next_action = (
                    "Candidate passed local quality gates. GitHub writes were simulated; "
                    "explicit authorization is required before creating a branch, commit, or draft PR."
                )

        if quality_status == "REVIEW_APPROVED_GENERATED_TESTS_PASSED":
            test_execution["status"] = "PASSED"
        elif quality_status == "REQUEST_CHANGES":
            test_execution["status"] = "BLOCKED_BY_REVIEW"
            test_execution["summary"] = "Tests blocked: Reviewer requested changes."
            next_action = "Reviewer requested changes. Manual review required."
        elif quality_status == "TEST_ENVIRONMENT_ERROR":
            test_execution["status"] = "TEST_ENVIRONMENT_ERROR"
            next_action = (
                f"Test environment error: {test_execution.get('summary', 'Docker error')}. "
                "Ensure Docker Desktop is running."
            )
        elif quality_status == "GENERATED_TESTS_FAILED":
            test_execution["status"] = "GENERATED_TESTS_FAILED"
            next_action = "Generated tests failed after retries. Manual review required."
        else:
            next_action = "Manual review required."

        proposal = proposal_store.create(
            repository=request.repo,
            issue_number=request.issue_number,
            target_branch=source_branch,
            target_path=target_path,
            operation=operation if target_path else None,
            proposed_content=proposed_code,
            diff_preview=diff_preview,
            quality_gate_status=quality_status,
            base_commit_sha=base_commit_sha,
            target_file_blob_sha=target_file_blob_sha,
        )

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
            "write_simulation": write_simulation,
            "target_path": target_path,
            "operation": operation,
            "files_changed": files_changed,
            "diff_preview": diff_preview,
            "diff_previews": diff_previews,
            "proposal_id": proposal.proposal_id,
            "proposal": proposal.public_dict(),
            "acceptance_criteria": acceptance_criteria,
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as error:
        raise github_http_error(error) from error
    except RateLimitError as error:
        raise handle_ai_rate_limit(error) from error
    except Exception as error:
        if is_rate_limit_error(error):
            raise handle_ai_rate_limit(error) from error
        raise HTTPException(
            status_code=500,
            detail=f"Workflow failed: {type(error).__name__}: {sanitize_secret(str(error))}",
        ) from error


