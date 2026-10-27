import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Optional
import httpx
from groq import RateLimitError

from backend.config import settings
from backend.github.client import GitHubClient

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.github.issues import IssueService
from backend.github.repository import RepositoryService
from backend.github.write_orchestrator import ProposalWriteOrchestrator
from backend.github.client import GitHubStaleRevisionError, GitHubWritesDisabledError
from backend.schemas import (
    DiagnoseRequest,
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
    WORKFLOW_READER,
)
from backend.workflows import (
    WorkflowStore,
    WorkflowStorageError,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_CANCELLED,
    VALID_STATUSES,
    AGENT_PENDING,
    AGENT_RUNNING,
    AGENT_COMPLETED,
    AGENT_FAILED,
    AGENT_SKIPPED,
    STAGE_INITIALIZING,
    STAGE_PLANNING,
    STAGE_CODING,
    STAGE_TESTING,
    STAGE_REVIEWING,
    STAGE_FINALIZING,
    STAGE_COMPLETED,
    STAGE_FAILED,
    EVENT_WORKFLOW_STARTED,
    EVENT_PLANNER_STARTED,
    EVENT_PLANNER_COMPLETED,
    EVENT_PLANNER_FAILED,
    EVENT_CODE_GENERATION_STARTED,
    EVENT_CODE_GENERATION_COMPLETED,
    EVENT_CODE_GENERATION_FAILED,
    EVENT_TESTS_STARTED,
    EVENT_TESTS_COMPLETED,
    EVENT_TESTS_FAILED,
    EVENT_REVIEWER_STARTED,
    EVENT_REVIEWER_COMPLETED,
    EVENT_REVIEWER_FAILED,
    EVENT_PROPOSAL_CREATED,
    EVENT_WORKFLOW_SUCCEEDED,
    EVENT_WORKFLOW_FAILED,
    EVENT_DIAGNOSIS_REQUESTED,
    EVENT_DIAGNOSIS_COMPLETED,
    EVENT_WORKFLOW_RETRIED,
)
import uuid
from backend.agents.planner import PlannerAgent
from backend.agents.coder import CoderAgent
from backend.agents.tester import TesterAgent
from backend.agents.test_runner import TestRunner
from backend.agents.reviewer import ReviewerAgent
from backend.agents.generated_test_runner import GeneratedTestRunner
from backend.agents.diagnostician import DiagnosticAgent
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
workflow_store = WorkflowStore(settings.WORKFLOW_DB_PATH)
diagnostic_agent = DiagnosticAgent()
proposal_write_orchestrator = ProposalWriteOrchestrator(proposal_store, repository_service, github_client)
approval_authenticator = BearerTokenAuthenticator.from_json(settings.APPROVAL_AUTH_TOKENS)
approval_rate_limiter = ApprovalRateLimiter()
WORKFLOW_READ_ROLES = (PROPOSAL_READER, PROPOSAL_APPROVER, WORKFLOW_READER)


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


def require_workflow_role():
    async def dependency(authorization: str | None = Header(default=None)) -> AuthenticatedIdentity:
        try:
            authenticator = get_approval_authenticator()
            identity = authenticator.authenticate(authorization)
            return authenticator.require_any_role(identity, WORKFLOW_READ_ROLES)
        except AuthenticationConfigurationError as error:
            raise HTTPException(status_code=503, detail="Workflow authentication is not configured.") from error
        except AuthenticationError as error:
            raise HTTPException(status_code=401, detail="Authentication credentials are required or invalid.", headers={"WWW-Authenticate": "Bearer"}) from error
        except AuthorizationError as error:
            raise HTTPException(status_code=403, detail="You are not authorized to perform this action.") from error

    return dependency


def _record_workflow_failure(
    wf_id: str,
    error_summary: str,
    failed_stage: str,
    active_agent: str | None = None,
) -> None:
    try:
        agent_updates = {}
        if active_agent == "planner":
            agent_updates["planner_status"] = AGENT_FAILED
        elif active_agent == "coder":
            agent_updates["coder_status"] = AGENT_FAILED
        elif active_agent == "tester":
            agent_updates["tester_status"] = AGENT_FAILED
        elif active_agent == "reviewer":
            agent_updates["reviewer_status"] = AGENT_FAILED

        workflow_store.update_workflow(
            wf_id,
            status=STATUS_FAILED,
            stage=failed_stage,
            error_summary=error_summary,
            **agent_updates,
        )
        workflow_store.record_event(
            wf_id,
            event_type=EVENT_WORKFLOW_FAILED,
            stage=failed_stage,
            message=f"Workflow failed at stage {failed_stage}: {error_summary}",
        )
    except Exception as exc:
        logger.warning("Failed to record workflow failure: %s", exc)



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


@app.get("/workflows")
async def list_workflows(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    repository: str | None = Query(default=None),
    _: AuthenticatedIdentity = Depends(require_workflow_role()),
):
    if status is not None:
        clean_status = status.strip().upper()
        if clean_status not in VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status filter. Allowed values: {', '.join(sorted(VALID_STATUSES))}",
            )
    try:
        workflows, total = workflow_store.list_workflows(
            limit=limit,
            offset=offset,
            status=status,
            repository=repository,
        )
        return {
            "status": "success",
            "workflows": [w.to_dict() for w in workflows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except WorkflowStorageError as error:
        raise HTTPException(status_code=503, detail="Workflow storage is temporarily unavailable.") from error


@app.get("/workflows/stats")
async def get_workflow_stats(
    _: AuthenticatedIdentity = Depends(require_workflow_role()),
):
    try:
        stats = workflow_store.get_stats()
        return {"status": "success", "stats": stats}
    except WorkflowStorageError as error:
        raise HTTPException(status_code=503, detail="Workflow storage is temporarily unavailable.") from error


@app.get("/workflows/{workflow_id}")
async def get_workflow_details(
    workflow_id: str,
    _: AuthenticatedIdentity = Depends(require_workflow_role()),
):
    try:
        workflow = workflow_store.get_workflow(workflow_id)
    except WorkflowStorageError as error:
        raise HTTPException(status_code=503, detail="Workflow storage is temporarily unavailable.") from error
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return {"status": "success", "workflow": workflow.to_dict()}


@app.get("/workflows/{workflow_id}/events")
async def get_workflow_events(
    workflow_id: str,
    _: AuthenticatedIdentity = Depends(require_workflow_role()),
):
    try:
        workflow = workflow_store.get_workflow(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        events = workflow_store.get_workflow_events(workflow_id)
        return {
            "status": "success",
            "workflow_id": workflow_id,
            "events": [e.to_dict() for e in events],
        }
    except HTTPException:
        raise
    except WorkflowStorageError as error:
        raise HTTPException(status_code=503, detail="Workflow storage is temporarily unavailable.") from error


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


        
async def execute_agent_workflow_internal(
    request: IssueRequest,
    retry_of_workflow_id: str | None = None,
) -> dict[str, Any]:
    workflow_id = str(uuid.uuid4())
    current_stage = STAGE_INITIALIZING
    active_agent = None

    try:
        try:
            workflow_store.create_workflow(
                workflow_id=workflow_id,
                repository=request.repo,
                issue_number=request.issue_number,
                status=STATUS_RUNNING,
                stage=STAGE_INITIALIZING,
                metadata={"target_path": request.target_path},
                retry_of_workflow_id=retry_of_workflow_id,
            )
            start_msg = (
                f"Workflow retry execution started for {request.repo}#{request.issue_number} (retrying {retry_of_workflow_id})."
                if retry_of_workflow_id
                else f"Workflow execution started for {request.repo}#{request.issue_number}."
            )
            workflow_store.record_event(
                workflow_id,
                event_type=EVENT_WORKFLOW_STARTED,
                stage=STAGE_INITIALIZING,
                message=start_msg,
            )
        except Exception as exc:
            logger.warning("Failed to initialize workflow execution record: %s", exc)

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

        current_stage = STAGE_PLANNING
        active_agent = "planner"
        try:
            workflow_store.update_workflow(
                workflow_id,
                stage=STAGE_PLANNING,
                planner_status=AGENT_RUNNING,
            )
            workflow_store.record_event(
                workflow_id,
                event_type=EVENT_PLANNER_STARTED,
                stage=STAGE_PLANNING,
                message="Planner agent analyzing repository context and requirements.",
            )
        except Exception as exc:
            logger.warning("Failed to record planner start: %s", exc)

        plan = planner_agent.create_plan(
            title=issue_title,
            description=issue_body,
            repository_context=repository_context,
            repo_files=relevant_repo_files,
            target_path=request.target_path or "",
        )

        try:
            workflow_store.update_workflow(
                workflow_id,
                planner_status=AGENT_COMPLETED,
            )
            workflow_store.record_event(
                workflow_id,
                event_type=EVENT_PLANNER_COMPLETED,
                stage=STAGE_PLANNING,
                message="Implementation plan generated successfully.",
            )
        except Exception as exc:
            logger.warning("Failed to record planner completion: %s", exc)

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

        current_stage = STAGE_CODING
        active_agent = "coder"
        try:
            workflow_store.update_workflow(
                workflow_id,
                stage=STAGE_CODING,
                coder_status=AGENT_RUNNING,
            )
            workflow_store.record_event(
                workflow_id,
                event_type=EVENT_CODE_GENERATION_STARTED,
                stage=STAGE_CODING,
                message=f"Coder generating candidate code ({operation} {target_path or 'new file'}).",
            )
        except Exception as exc:
            logger.warning("Failed to record code generation start: %s", exc)

        proposed_code = coder_agent.generate_code(
            task=task,
            implementation_plan=str(plan),
            existing_code=existing_code,
            operation=operation,
            file_path=target_path or "",
            acceptance_criteria=acceptance_criteria,
        )

        try:
            workflow_store.update_workflow(
                workflow_id,
                coder_status=AGENT_COMPLETED,
            )
            workflow_store.record_event(
                workflow_id,
                event_type=EVENT_CODE_GENERATION_COMPLETED,
                stage=STAGE_CODING,
                message="Candidate code generation completed.",
            )
        except Exception as exc:
            logger.warning("Failed to record code generation completion: %s", exc)

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
            current_stage = STAGE_REVIEWING
            active_agent = "reviewer"
            try:
                workflow_store.update_workflow(
                    workflow_id,
                    stage=STAGE_REVIEWING,
                    reviewer_status=AGENT_RUNNING,
                )
                workflow_store.record_event(
                    workflow_id,
                    event_type=EVENT_REVIEWER_STARTED,
                    stage=STAGE_REVIEWING,
                    message=f"Reviewer evaluating candidate code (attempt {attempt + 1}).",
                )
            except Exception as exc:
                logger.warning("Failed to record reviewer start: %s", exc)

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
                try:
                    workflow_store.record_event(
                        workflow_id,
                        event_type=EVENT_REVIEWER_COMPLETED,
                        stage=STAGE_REVIEWING,
                        message=f"Reviewer requested changes (attempt {attempt + 1}).",
                    )
                except Exception as exc:
                    logger.warning("Failed to record reviewer event: %s", exc)

                if attempt < max_retries:
                    current_stage = STAGE_CODING
                    active_agent = "coder"
                    try:
                        workflow_store.update_workflow(
                            workflow_id,
                            stage=STAGE_CODING,
                            coder_status=AGENT_RUNNING,
                        )
                        workflow_store.record_event(
                            workflow_id,
                            event_type=EVENT_CODE_GENERATION_STARTED,
                            stage=STAGE_CODING,
                            message=f"Coder revising code based on reviewer feedback (attempt {attempt + 1}).",
                        )
                    except Exception as exc:
                        logger.warning("Failed to record coder revision start: %s", exc)

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

                    try:
                        workflow_store.update_workflow(
                            workflow_id,
                            coder_status=AGENT_COMPLETED,
                        )
                        workflow_store.record_event(
                            workflow_id,
                            event_type=EVENT_CODE_GENERATION_COMPLETED,
                            stage=STAGE_CODING,
                            message=f"Coder revision completed (attempt {attempt + 1}).",
                        )
                    except Exception as exc:
                        logger.warning("Failed to record coder revision completion: %s", exc)

                    continue
                break

            if "VERDICT: APPROVE" not in verdict:
                quality_status = "NEEDS_MANUAL_REVIEW"
                try:
                    workflow_store.update_workflow(
                        workflow_id,
                        reviewer_status=AGENT_COMPLETED,
                    )
                    workflow_store.record_event(
                        workflow_id,
                        event_type=EVENT_REVIEWER_COMPLETED,
                        stage=STAGE_REVIEWING,
                        message="Reviewer finished evaluation (verdict inconclusive).",
                    )
                except Exception as exc:
                    logger.warning("Failed to record reviewer completion: %s", exc)
                break

            try:
                workflow_store.update_workflow(
                    workflow_id,
                    reviewer_status=AGENT_COMPLETED,
                )
                workflow_store.record_event(
                    workflow_id,
                    event_type=EVENT_REVIEWER_COMPLETED,
                    stage=STAGE_REVIEWING,
                    message="Reviewer approved candidate code.",
                )
            except Exception as exc:
                logger.warning("Failed to record reviewer approval: %s", exc)

            current_stage = STAGE_TESTING
            active_agent = "tester"
            try:
                workflow_store.update_workflow(
                    workflow_id,
                    stage=STAGE_TESTING,
                    tester_status=AGENT_RUNNING,
                )
            except Exception as exc:
                logger.warning("Failed to update tester status: %s", exc)

            if not test_suggestions:
                try:
                    workflow_store.record_event(
                        workflow_id,
                        event_type=EVENT_TESTS_STARTED,
                        stage=STAGE_TESTING,
                        message="Tester generating isolated unit tests.",
                    )
                except Exception as exc:
                    logger.warning("Failed to record test generation event: %s", exc)

                test_suggestions = tester_agent.generate_tests(
                    task=task,
                    proposed_code=proposed_code,
                )

            try:
                workflow_store.record_event(
                    workflow_id,
                    event_type=EVENT_TESTS_STARTED,
                    stage=STAGE_TESTING,
                    message="Executing generated tests in sandbox environment.",
                )
            except Exception as exc:
                logger.warning("Failed to record tests start event: %s", exc)

            test_execution = generated_test_runner.run_generated_tests(
                proposed_code=proposed_code,
                generated_tests=test_suggestions,
            )
            if test_execution["passed"]:
                quality_status = "REVIEW_APPROVED_GENERATED_TESTS_PASSED"
                try:
                    workflow_store.update_workflow(
                        workflow_id,
                        tester_status=AGENT_COMPLETED,
                    )
                    workflow_store.record_event(
                        workflow_id,
                        event_type=EVENT_TESTS_COMPLETED,
                        stage=STAGE_TESTING,
                        message=f"Generated tests passed: {test_execution.get('summary', 'Tests succeeded.')}",
                    )
                except Exception as exc:
                    logger.warning("Failed to record test completion: %s", exc)
                break

            if test_execution.get("status") == "DOCKER_ERROR":
                quality_status = "TEST_ENVIRONMENT_ERROR"
                try:
                    workflow_store.update_workflow(
                        workflow_id,
                        tester_status=AGENT_FAILED,
                    )
                    workflow_store.record_event(
                        workflow_id,
                        event_type=EVENT_TESTS_FAILED,
                        stage=STAGE_TESTING,
                        message=f"Test environment error: {test_execution.get('summary', 'Docker error')}",
                    )
                except Exception as exc:
                    logger.warning("Failed to record test failure: %s", exc)
                break

            quality_status = "GENERATED_TESTS_FAILED"
            try:
                workflow_store.record_event(
                    workflow_id,
                    event_type=EVENT_TESTS_FAILED,
                    stage=STAGE_TESTING,
                    message=f"Generated tests failed: {test_execution.get('summary', 'Test failure')}",
                )
            except Exception as exc:
                logger.warning("Failed to record test failure: %s", exc)

            if attempt < max_retries:
                current_stage = STAGE_CODING
                active_agent = "coder"
                try:
                    workflow_store.update_workflow(
                        workflow_id,
                        stage=STAGE_CODING,
                        coder_status=AGENT_RUNNING,
                    )
                    workflow_store.record_event(
                        workflow_id,
                        event_type=EVENT_CODE_GENERATION_STARTED,
                        stage=STAGE_CODING,
                        message=f"Coder revising code after test failure (attempt {attempt + 1}).",
                    )
                except Exception as exc:
                    logger.warning("Failed to record coder revision after test failure: %s", exc)

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

                try:
                    workflow_store.update_workflow(
                        workflow_id,
                        coder_status=AGENT_COMPLETED,
                    )
                    workflow_store.record_event(
                        workflow_id,
                        event_type=EVENT_CODE_GENERATION_COMPLETED,
                        stage=STAGE_CODING,
                        message=f"Coder revision completed (attempt {attempt + 1}).",
                    )
                except Exception as exc:
                    logger.warning("Failed to record coder revision completion: %s", exc)

                continue
            break

        # This endpoint deliberately never mutates GitHub. A successful local
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

        current_stage = STAGE_FINALIZING
        active_agent = None

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

        try:
            workflow_store.record_event(
                workflow_id,
                event_type=EVENT_PROPOSAL_CREATED,
                stage=STAGE_FINALIZING,
                message=f"Proposal {proposal.proposal_id} created with status {proposal.status}.",
            )
            if quality_status == "REVIEW_APPROVED_GENERATED_TESTS_PASSED":
                workflow_store.update_workflow(
                    workflow_id,
                    status=STATUS_SUCCESS,
                    stage=STAGE_COMPLETED,
                    proposal_id=proposal.proposal_id,
                )
                workflow_store.record_event(
                    workflow_id,
                    event_type=EVENT_WORKFLOW_SUCCEEDED,
                    stage=STAGE_COMPLETED,
                    message="Workflow execution succeeded; quality gates passed.",
                )
            else:
                gate_failed_stage = STAGE_TESTING if "TESTS" in quality_status else STAGE_REVIEWING
                workflow_store.update_workflow(
                    workflow_id,
                    status=STATUS_FAILED,
                    stage=gate_failed_stage,
                    error_summary=f"Quality gate not met: {quality_status} ({next_action})",
                    proposal_id=proposal.proposal_id,
                )
                workflow_store.record_event(
                    workflow_id,
                    event_type=EVENT_WORKFLOW_FAILED,
                    stage=gate_failed_stage,
                    message=f"Workflow failed quality gate: {quality_status}.",
                )
        except Exception as exc:
            logger.warning("Failed to record workflow completion status: %s", exc)

        return {
            "status": "success",
            "workflow_id": workflow_id,
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
            "retry_of_workflow_id": retry_of_workflow_id,
        }

    except HTTPException as error:
        _record_workflow_failure(workflow_id, str(error.detail), current_stage, active_agent)
        raise
    except httpx.HTTPStatusError as error:
        exc = github_http_error(error)
        _record_workflow_failure(workflow_id, str(exc.detail), current_stage, active_agent)
        raise exc from error
    except RateLimitError as error:
        exc = handle_ai_rate_limit(error)
        _record_workflow_failure(workflow_id, str(exc.detail), current_stage, active_agent)
        raise exc from error
    except Exception as error:
        if is_rate_limit_error(error):
            exc = handle_ai_rate_limit(error)
            _record_workflow_failure(workflow_id, str(exc.detail), current_stage, active_agent)
            raise exc from error
        sanitized = sanitize_secret(str(error))
        _record_workflow_failure(workflow_id, f"{type(error).__name__}: {sanitized}", current_stage, active_agent)
        raise HTTPException(
            status_code=500,
            detail=f"Workflow failed: {type(error).__name__}: {sanitized}",
        ) from error


@app.post("/agents/workflow")
async def run_agent_workflow(request: IssueRequest):
    return await execute_agent_workflow_internal(request=request, retry_of_workflow_id=None)


@app.post("/workflows/{workflow_id}/diagnose")
async def diagnose_workflow(
    workflow_id: str,
    payload: Optional[DiagnoseRequest] = Body(default=None),
    force_refresh: bool = Query(default=False),
    _: AuthenticatedIdentity = Depends(require_workflow_role()),
):
    is_refresh = force_refresh or (payload.force_refresh if payload else False)
    try:
        workflow = workflow_store.get_workflow(workflow_id)
    except WorkflowStorageError as error:
        raise HTTPException(status_code=503, detail="Workflow storage is temporarily unavailable.") from error

    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    if workflow.status != STATUS_FAILED:
        return {
            "status": "success",
            "workflow_id": workflow_id,
            "cached": False,
            "diagnosis": {
                "summary": f"Workflow is currently in {workflow.status} status and has not recorded any failure.",
                "failed_stage": workflow.stage,
                "probable_root_cause": "N/A - Workflow did not fail.",
                "supporting_evidence": [],
                "recommended_steps": ["No diagnostic required for non-failed workflows."],
                "suggested_fix": None,
                "confidence": "LOW",
            },
        }

    if not is_refresh and workflow.diagnosis_json:
        try:
            cached_diag = json.loads(workflow.diagnosis_json)
            return {
                "status": "success",
                "workflow_id": workflow_id,
                "cached": True,
                "diagnosis": cached_diag,
            }
        except Exception:
            pass

    try:
        events = workflow_store.get_workflow_events(workflow_id)
        event_dicts = [e.to_dict() for e in events]
        workflow_store.record_event(
            workflow_id,
            event_type=EVENT_DIAGNOSIS_REQUESTED,
            stage=workflow.stage,
            message="AI failure diagnosis requested.",
        )
    except WorkflowStorageError as error:
        raise HTTPException(status_code=503, detail="Workflow storage is temporarily unavailable.") from error

    agent_statuses = {
        "planner": workflow.planner_status,
        "coder": workflow.coder_status,
        "tester": workflow.tester_status,
        "reviewer": workflow.reviewer_status,
    }

    metadata = {}
    if workflow.metadata_json:
        try:
            metadata = json.loads(workflow.metadata_json)
        except Exception:
            metadata = {}

    diagnosis = diagnostic_agent.diagnose(
        stage=workflow.stage,
        error_summary=workflow.error_summary,
        agent_statuses=agent_statuses,
        events=event_dicts,
        repository=workflow.repository,
        issue_number=workflow.issue_number,
        metadata=metadata,
    )

    try:
        workflow_store.store_diagnosis(workflow_id, diagnosis)
        workflow_store.record_event(
            workflow_id,
            event_type=EVENT_DIAGNOSIS_COMPLETED,
            stage=workflow.stage,
            message=f"AI failure diagnosis completed with confidence {diagnosis.get('confidence')}.",
        )
    except Exception as exc:
        logger.warning("Failed to store diagnosis for %s: %s", workflow_id, exc)

    return {
        "status": "success",
        "workflow_id": workflow_id,
        "cached": False,
        "diagnosis": diagnosis,
    }


@app.post("/workflows/{workflow_id}/retry")
async def retry_workflow(
    workflow_id: str,
    _: AuthenticatedIdentity = Depends(require_workflow_role()),
):
    try:
        orig = workflow_store.get_workflow(workflow_id)
    except WorkflowStorageError as error:
        raise HTTPException(status_code=503, detail="Workflow storage is temporarily unavailable.") from error

    if orig is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    if orig.status != STATUS_FAILED:
        raise HTTPException(status_code=400, detail=f"Only failed workflows can be retried (current status: {orig.status}).")

    if not orig.repository or not orig.issue_number:
        raise HTTPException(status_code=400, detail="Original workflow does not have valid repository or issue details.")

    # Check non-retryable errors (e.g. permanent authentication failures)
    err = (orig.error_summary or "").lower()
    if any(phrase in err for phrase in ["401", "authentication failed", "invalid token", "bad credentials"]):
        raise HTTPException(status_code=400, detail="This workflow failed due to an authentication error. Please verify credentials before retrying.")

    # Deduplicate in-flight retries
    retries = workflow_store.find_retries(workflow_id)
    active_retries = [r for r in retries if r.status in {STATUS_RUNNING, STATUS_PENDING}]
    if active_retries:
        raise HTTPException(status_code=409, detail=f"A retry for workflow {workflow_id} is already in progress ({active_retries[0].workflow_id}).")

    # Prevent infinite retry loops (depth limit 3)
    chain_depth = 0
    curr = orig
    while curr and curr.retry_of_workflow_id and chain_depth < 5:
        chain_depth += 1
        curr = workflow_store.get_workflow(curr.retry_of_workflow_id)
    if chain_depth >= 3:
        raise HTTPException(status_code=400, detail="Maximum retry limit reached for this workflow chain (3 retries). Manual intervention required.")

    target_path = None
    if orig.metadata_json:
        try:
            m = json.loads(orig.metadata_json)
            target_path = m.get("target_path") or None
        except Exception:
            target_path = None

    try:
        workflow_store.record_event(
            workflow_id,
            event_type=EVENT_WORKFLOW_RETRIED,
            stage=orig.stage,
            message="Workflow retry initiated.",
        )
    except Exception as exc:
        logger.warning("Failed to record retry event on original workflow %s: %s", workflow_id, exc)

    req = IssueRequest(
        repo=orig.repository,
        issue_number=orig.issue_number,
        target_path=target_path,
    )
    return await execute_agent_workflow_internal(request=req, retry_of_workflow_id=workflow_id)


@app.post("/workflows/{workflow_id}/fix-proposal")
async def create_fix_proposal_from_diagnosis(
    workflow_id: str,
    _: AuthenticatedIdentity = Depends(require_workflow_role()),
):
    try:
        workflow = workflow_store.get_workflow(workflow_id)
    except WorkflowStorageError as error:
        raise HTTPException(status_code=503, detail="Workflow storage is temporarily unavailable.") from error

    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")

    diagnosis = workflow_store.get_diagnosis(workflow_id)
    if not diagnosis or not diagnosis.get("suggested_fix"):
        raise HTTPException(status_code=400, detail="No suggested fix available in diagnosis for this workflow.")

    suggested_code = diagnosis["suggested_fix"].strip()
    target_path = None
    if workflow.metadata_json:
        try:
            m = json.loads(workflow.metadata_json)
            target_path = m.get("target_path")
        except Exception:
            pass

    if not target_path or not is_safe_relative_path(target_path):
        target_path = "solution.py"

    repository_data = await repository_service.get_repository(repo=workflow.repository)
    source_branch = repository_data.get("default_branch") or "main"
    base_commit_sha = await repository_service.get_branch_sha(workflow.repository, source_branch)

    existing_content = ""
    target_file_blob_sha = None
    operation = "create"
    try:
        file_data = await repository_service.get_file(repo=workflow.repository, path=target_path, branch=source_branch)
        operation = "modify"
        existing_content = file_data.get("decoded_content", "")
        target_file_blob_sha = file_data.get("sha")
    except Exception:
        existing_content = ""
        operation = "create"

    diff_preview = build_diff_preview(
        path=target_path,
        operation=operation,
        proposed_content=suggested_code,
        original_content=existing_content,
        original_content_available=True,
    )

    proposal = proposal_store.create(
        repository=workflow.repository,
        issue_number=workflow.issue_number,
        target_branch=source_branch,
        target_path=target_path,
        operation=operation,
        proposed_content=suggested_code,
        diff_preview=diff_preview,
        quality_gate_status="DIAGNOSTIC_FIX_SUGGESTION",
        base_commit_sha=base_commit_sha,
        target_file_blob_sha=target_file_blob_sha,
    )

    if not workflow.proposal_id:
        workflow_store.update_workflow(workflow_id, proposal_id=proposal.proposal_id)
        workflow_store.record_event(
            workflow_id,
            event_type=EVENT_PROPOSAL_CREATED,
            stage=workflow.stage,
            message=f"Diagnostic fix proposal {proposal.proposal_id} created with status {proposal.status}.",
        )

    return {
        "status": "success",
        "message": "Candidate patch created as a pending proposal requiring human review.",
        "proposal_id": proposal.proposal_id,
        "proposal": proposal.public_dict(),
        "diff_preview": diff_preview,
    }


