"""Comprehensive regression test suite for DevSquad Review & Test stage.

Covers all 15 required scenarios:
1. A valid candidate passes generated tests.
2. A candidate fails and receives actionable test feedback.
3. A candidate is successfully repaired on retry.
4. A candidate continues failing after all retries.
5. Reviewer returns APPROVE while generated tests fail.
6. Async generated tests execute correctly in the isolated environment.
7. Existing GitHub issue creation behavior remains compatible.
8. Existing repository validation remains intact.
9. HTTP error responses are handled correctly.
10. Malformed responses are handled correctly.
11. Docker cleanup occurs after successful execution.
12. Docker cleanup occurs after failed execution.
13. Docker infrastructure failures are classified correctly.
14. Previous attempt results cannot override the latest attempt.
15. Workflow history preserves the correct stage, attempt count, and terminal status.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.agents.generated_test_runner import GeneratedTestRunner
from backend.agents.reviewer import ReviewerAgent
from backend.config import settings
from backend.github.client import GitHubClient, GitHubWritesDisabledError
from backend.github.issues import IssueService, create_test_issue
from backend.main import app
from backend.patch_validator import validate_candidate_patch, validate_target_path
from backend.proposals import ProposalStore
from backend.workflows import (
    EVENT_CODE_GENERATION_STARTED,
    EVENT_REVIEWER_COMPLETED,
    EVENT_TESTS_FAILED,
    EVENT_WORKFLOW_FAILED,
    STAGE_CODING,
    STAGE_REVIEWING,
    STAGE_TESTING,
    STATUS_FAILED,
    STATUS_SUCCESS,
    WorkflowStore,
)

client = TestClient(app)


# ==============================================================================
# Scenario 1: A valid candidate passes generated tests
# ==============================================================================
def test_scenario_1_valid_candidate_passes_generated_tests(monkeypatch):
    runner = GeneratedTestRunner()
    monkeypatch.setattr(runner, "_ensure_image", lambda: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        ),
    )

    result = runner.run_generated_tests(
        proposed_code="def add(a: int, b: int) -> int:\n    return a + b\n",
        generated_tests="from candidate import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    )

    assert result["status"] == "TESTS_PASSED"
    assert result["passed"] is True
    assert result["return_code"] == 0
    assert result["infrastructure_error"] is False


# ==============================================================================
# Scenario 2: A candidate fails and receives actionable test feedback
# ==============================================================================
def test_scenario_2_candidate_fails_and_extracts_actionable_feedback(monkeypatch):
    runner = GeneratedTestRunner()
    monkeypatch.setattr(runner, "_ensure_image", lambda: None)

    failure_stdout = (
        "F                                                                        [100%]\n"
        "=================================== FAILURES ===================================\n"
        "__________________________________ test_fail ___________________________________\n"
        "candidate.py:2: in broken\n"
        "    raise ValueError('Something is wrong')\n"
        "E   ValueError: Something is wrong\n"
        "=========================== short test summary info ============================\n"
        "FAILED test_candidate.py::test_fail - ValueError: Something is wrong\n"
        "1 failed in 0.12s\n"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout=failure_stdout, stderr=""
        ),
    )

    result = runner.run_generated_tests(
        proposed_code="def broken():\n    raise ValueError('Something is wrong')\n",
        generated_tests="from candidate import broken\n\ndef test_fail():\n    broken()\n",
    )

    assert result["status"] == "TESTS_FAILED"
    assert result["passed"] is False
    assert "test_fail" in result["failed_tests"]
    assert "ValueError: Something is wrong" in result["tracebacks"]
    assert result["infrastructure_error"] is False


# ==============================================================================
# Scenario 3: A candidate is successfully repaired on retry
# ==============================================================================
def test_scenario_3_candidate_successfully_repaired_on_retry(monkeypatch, tmp_path):
    wf_db = tmp_path / "test_wf_repair.sqlite3"
    prop_db = tmp_path / "test_prop_repair.sqlite3"
    wf_store = WorkflowStore(wf_db)
    prop_store = ProposalStore(prop_db)
    monkeypatch.setattr("backend.main.workflow_store", wf_store)
    monkeypatch.setattr("backend.main.proposal_store", prop_store)

    # Attempt 0: fails tests. Attempt 1: passes tests.
    test_results = [
        {
            "status": "TESTS_FAILED",
            "passed": False,
            "summary": "1 failed in 0.1s",
            "return_code": 1,
            "failed_tests": ["test_compute"],
            "tracebacks": "assert compute() == 42",
            "infrastructure_error": False,
        },
        {
            "status": "TESTS_PASSED",
            "passed": True,
            "summary": "1 passed in 0.1s",
            "return_code": 0,
            "failed_tests": [],
            "tracebacks": "",
            "infrastructure_error": False,
        },
    ]

    coder_codes = [
        "def compute():\n    return 40\n",  # initial
        "def compute():\n    return 42\n",  # repaired
    ]

    with (
        patch("backend.main.issue_service.get_issue", new_callable=AsyncMock) as mock_get_issue,
        patch("backend.main.planner_agent.create_plan", return_value={"steps": ["Plan to compute"]}),
        patch("backend.main.coder_agent.generate_code", return_value=coder_codes[0]),
        patch("backend.main.coder_agent.improve_code", return_value=coder_codes[1]) as mock_improve,
        patch("backend.main.reviewer_agent.review_code", return_value="VERDICT: APPROVE\nIssues: None\nFixes: None"),
        patch("backend.main.tester_agent.generate_tests", return_value="from candidate import compute\ndef test_compute(): assert compute() == 42\n"),
        patch("backend.main.generated_test_runner.run_generated_tests", side_effect=test_results),
    ):
        mock_get_issue.return_value = {"title": "Fix compute", "body": "Return 42", "number": 1, "state": "open"}
        response = client.post("/agents/workflow", json={"repo": "om-3531/DevSquad", "issue_number": 1})

    assert response.status_code == 200
    data = response.json()
    assert data["quality_gate"]["status"] == "REVIEW_APPROVED_GENERATED_TESTS_PASSED"
    assert data["quality_gate"]["approved"] is True
    assert data["correction_attempts"] == 1
    mock_improve.assert_called_once()
    # Ensure retry received failed test names in feedback
    retry_feedback = mock_improve.call_args.kwargs["reviewer_feedback"]
    assert "test_compute" in retry_feedback


# ==============================================================================
# Scenario 4: A candidate continues failing after all retries
# ==============================================================================
def test_scenario_4_candidate_continues_failing_after_all_retries(monkeypatch, tmp_path):
    wf_db = tmp_path / "test_wf_fail_exhaust.sqlite3"
    prop_db = tmp_path / "test_prop_fail_exhaust.sqlite3"
    wf_store = WorkflowStore(wf_db)
    prop_store = ProposalStore(prop_db)
    monkeypatch.setattr("backend.main.workflow_store", wf_store)
    monkeypatch.setattr("backend.main.proposal_store", prop_store)

    failing_result = {
        "status": "TESTS_FAILED",
        "passed": False,
        "summary": "1 failed in 0.1s",
        "return_code": 1,
        "failed_tests": ["test_compute"],
        "tracebacks": "assert compute() == 42",
        "infrastructure_error": False,
    }

    with (
        patch("backend.main.issue_service.get_issue", new_callable=AsyncMock) as mock_get_issue,
        patch("backend.main.planner_agent.create_plan", return_value={"steps": ["Plan to compute"]}),
        patch("backend.main.coder_agent.generate_code", return_value="def compute(): return 0\n"),
        patch("backend.main.coder_agent.improve_code", return_value="def compute(): return 0\n"),
        patch("backend.main.reviewer_agent.review_code", return_value="VERDICT: APPROVE\nIssues: None\nFixes: None"),
        patch("backend.main.tester_agent.generate_tests", return_value="def test_compute(): pass"),
        patch("backend.main.generated_test_runner.run_generated_tests", return_value=failing_result),
    ):
        mock_get_issue.return_value = {"title": "Fix compute", "body": "Return 42", "number": 1, "state": "open"}
        response = client.post("/agents/workflow", json={"repo": "om-3531/DevSquad", "issue_number": 1})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["quality_gate"]["status"] == "GENERATED_TESTS_FAILED"
    assert data["quality_gate"]["approved"] is False
    assert data["correction_attempts"] == 2
    # Verify workflow terminal state in database
    wf = wf_store.get_workflow(data["workflow_id"])
    assert wf.status == STATUS_FAILED
    assert wf.stage == STAGE_TESTING


# ==============================================================================
# Scenario 5: Reviewer returns APPROVE while generated tests fail
# ==============================================================================
def test_scenario_5_reviewer_approves_while_generated_tests_fail(monkeypatch, tmp_path):
    wf_db = tmp_path / "test_wf_approve_test_fail.sqlite3"
    prop_db = tmp_path / "test_prop_approve_test_fail.sqlite3"
    wf_store = WorkflowStore(wf_db)
    prop_store = ProposalStore(prop_db)
    monkeypatch.setattr("backend.main.workflow_store", wf_store)
    monkeypatch.setattr("backend.main.proposal_store", prop_store)

    failing_result = {
        "status": "TESTS_FAILED",
        "passed": False,
        "summary": "2 failed in 0.2s",
        "return_code": 1,
        "failed_tests": ["test_a", "test_b"],
        "tracebacks": "Failure details",
        "infrastructure_error": False,
    }

    with (
        patch("backend.main.issue_service.get_issue", new_callable=AsyncMock) as mock_get_issue,
        patch("backend.main.planner_agent.create_plan", return_value={"steps": ["Plan"]}),
        patch("backend.main.coder_agent.generate_code", return_value="def sample(): pass\n"),
        patch("backend.main.coder_agent.improve_code", return_value="def sample(): pass\n"),
        patch("backend.main.reviewer_agent.review_code", return_value="VERDICT: APPROVE\nIssues: None\nFixes: None"),
        patch("backend.main.tester_agent.generate_tests", return_value="def test_sample(): pass"),
        patch("backend.main.generated_test_runner.run_generated_tests", return_value=failing_result),
    ):
        mock_get_issue.return_value = {"title": "Sample", "body": "Body", "number": 1, "state": "open"}
        response = client.post("/agents/workflow", json={"repo": "om-3531/DevSquad", "issue_number": 1})

    assert response.status_code == 200, response.text
    data = response.json()
    # Reviewer approval MUST NOT override failing tests!
    assert data["quality_gate"]["status"] == "GENERATED_TESTS_FAILED"
    assert data["quality_gate"]["approved"] is False
    assert data["testing_status"] == "GENERATED_TESTS_FAILED"


# ==============================================================================
# Scenario 6: Async generated tests execute correctly in the isolated environment
# ==============================================================================
@pytest.mark.integration
def test_scenario_6_async_generated_tests_execute_in_isolated_docker_environment():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI not available on PATH.")

    runner = GeneratedTestRunner()
    async_candidate = (
        "async def async_operation(x: int) -> int:\n"
        "    return x * 2\n"
    )
    async_tests = (
        "import pytest\n"
        "from candidate import async_operation\n\n"
        "@pytest.mark.asyncio\n"
        "async def test_async_operation():\n"
        "    val = await async_operation(21)\n"
        "    assert val == 42\n"
    )

    result = runner.run_generated_tests(async_candidate, async_tests)
    assert result["status"] == "TESTS_PASSED", result["summary"]
    assert result["passed"] is True
    assert "PytestUnknownMarkWarning" not in result["stderr"]
    assert "PytestUnknownMarkWarning" not in result["stdout"]


# ==============================================================================
# Scenario 7: Existing GitHub issue creation behavior remains compatible
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_7_existing_github_issue_creation_compatible():
    mock_client = MagicMock()
    mock_client.create_issue = AsyncMock(
        return_value={"id": 999, "html_url": "https://github.com/om-3531/DevSquad/issues/999"}
    )
    service = IssueService(github_client=mock_client)
    res = await service.create_issue("om-3531/DevSquad", "Test Title", "Test Body")

    assert res == {"id": 999, "html_url": "https://github.com/om-3531/DevSquad/issues/999"}
    mock_client.create_issue.assert_awaited_once_with(
        repo="om-3531/DevSquad", title="Test Title", body="Test Body"
    )

    # Test create_test_issue wrapper passes supplied client and returns html_url
    url = await create_test_issue(
        repo="om-3531/DevSquad",
        title="Wrapper Issue",
        body="Wrapper Body",
        client=mock_client,
    )
    assert url == "https://github.com/om-3531/DevSquad/issues/999"
    mock_client.create_issue.assert_awaited_with(
        repo="om-3531/DevSquad", title="Wrapper Issue", body="Wrapper Body"
    )


# ==============================================================================
# Scenario 8: Existing repository validation remains intact
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_8_repository_validation_remains_intact():
    service = IssueService()
    # Path traversal or invalid repo format must be rejected and match both error contracts
    with pytest.raises(ValueError, match="Invalid repo"):
        await service.create_issue("owner/repo/extra", "Title")

    with pytest.raises(ValueError, match="Invalid GitHub repository identifier"):
        await service.create_issue("invalid_no_slash", "Title")

    with pytest.raises(ValueError, match="Invalid repo"):
        await service.create_issue("", "Title")


# ==============================================================================
# Scenario 9: HTTP error responses are handled correctly
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_9_http_error_responses_handled_correctly(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    service = IssueService()
    req = httpx.Request("POST", "https://api.github.com")

    # 404 -> FileNotFoundError
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = httpx.Response(404, request=req)
        with pytest.raises(FileNotFoundError, match="not found"):
            await service.create_issue("owner/repo", "Title")

    # 401/403 -> PermissionError
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = httpx.Response(403, request=req)
        with pytest.raises(PermissionError, match="permission denied"):
            await service.create_issue("owner/repo", "Title")

    # 429 -> RuntimeError
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = httpx.Response(429, request=req)
        with pytest.raises(RuntimeError, match="rate limit"):
            await service.create_issue("owner/repo", "Title")

    # 500 -> RuntimeError
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = httpx.Response(502, request=req)
        with pytest.raises(RuntimeError, match="server error"):
            await service.create_issue("owner/repo", "Title")


# ==============================================================================
# Scenario 10: Malformed responses are handled correctly
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_10_malformed_responses_handled_correctly():
    mock_client = MagicMock()
    service = IssueService(github_client=mock_client)

    # Empty title
    with pytest.raises(ValueError, match="cannot be empty"):
        await service.create_issue("owner/repo", "   ")

    # Missing id
    mock_client.create_issue = AsyncMock(return_value={"html_url": "https://github.com/..."})
    with pytest.raises(ValueError, match="Malformed GitHub API response"):
        await service.create_issue("owner/repo", "Valid Title")

    # Non-int id (bool)
    mock_client.create_issue = AsyncMock(return_value={"id": True, "html_url": "https://github.com/..."})
    with pytest.raises(ValueError, match="Malformed GitHub API response"):
        await service.create_issue("owner/repo", "Valid Title")

    # Empty html_url
    mock_client.create_issue = AsyncMock(return_value={"id": 123, "html_url": " "})
    with pytest.raises(ValueError, match="Malformed GitHub API response"):
        await service.create_issue("owner/repo", "Valid Title")


# ==============================================================================
# Scenario 11: Docker cleanup occurs after successful execution
# ==============================================================================
@pytest.mark.integration
def test_scenario_11_docker_cleanup_after_successful_execution():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI not available on PATH.")

    runner = GeneratedTestRunner()
    before = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"ancestor={runner.IMAGE_NAME}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert before.returncode == 0

    res = runner.run_generated_tests(
        proposed_code="def answer(): return 42\n",
        generated_tests="from candidate import answer\ndef test_a(): assert answer() == 42\n",
    )
    assert res["passed"] is True

    after = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"ancestor={runner.IMAGE_NAME}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert after.returncode == 0
    assert after.stdout.split() == before.stdout.split()


# ==============================================================================
# Scenario 12: Docker cleanup occurs after failed execution
# ==============================================================================
@pytest.mark.integration
def test_scenario_12_docker_cleanup_after_failed_execution():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI not available on PATH.")

    runner = GeneratedTestRunner()
    before = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"ancestor={runner.IMAGE_NAME}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert before.returncode == 0

    res = runner.run_generated_tests(
        proposed_code="def answer(): return 0\n",
        generated_tests="from candidate import answer\ndef test_a(): assert answer() == 42\n",
    )
    assert res["passed"] is False

    after = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"ancestor={runner.IMAGE_NAME}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert after.returncode == 0
    assert after.stdout.split() == before.stdout.split()


# ==============================================================================
# Scenario 13: Docker infrastructure failures are classified correctly
# ==============================================================================
def test_scenario_13_docker_infrastructure_failures_classified_correctly(monkeypatch):
    runner = GeneratedTestRunner()

    # Case A: Docker daemon unavailable
    monkeypatch.setattr(runner, "_ensure_image", lambda: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout="",
            stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
        ),
    )

    result = runner.run_generated_tests("def f(): pass", "def test_f(): pass")
    assert result["status"] == "DOCKER_ERROR"
    assert result["passed"] is False
    assert result["infrastructure_error"] is True

    # Case B: Docker CLI executable not found
    def raise_fnf(*args, **kwargs):
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(subprocess, "run", raise_fnf)
    result_cli = runner.run_generated_tests("def f(): pass", "def test_f(): pass")
    assert result_cli["status"] == "DOCKER_ERROR"
    assert result_cli["passed"] is False
    assert result_cli["infrastructure_error"] is True


# ==============================================================================
# Scenario 14: Previous attempt results cannot override the latest attempt
# ==============================================================================
def test_scenario_14_previous_attempt_results_do_not_override_latest_attempt(monkeypatch, tmp_path):
    wf_db = tmp_path / "test_wf_override.sqlite3"
    prop_db = tmp_path / "test_prop_override.sqlite3"
    wf_store = WorkflowStore(wf_db)
    prop_store = ProposalStore(prop_db)
    monkeypatch.setattr("backend.main.workflow_store", wf_store)
    monkeypatch.setattr("backend.main.proposal_store", prop_store)

    attempt_results = [
        {"status": "TESTS_FAILED", "passed": False, "summary": "attempt 1 failed", "return_code": 1, "failed_tests": ["test_old"], "tracebacks": "", "infrastructure_error": False},
        {"status": "TESTS_FAILED", "passed": False, "summary": "attempt 2 failed with different error", "return_code": 1, "failed_tests": ["test_new_failure"], "tracebacks": "", "infrastructure_error": False},
        {"status": "TESTS_PASSED", "passed": True, "summary": "attempt 3 passed completely", "return_code": 0, "failed_tests": [], "tracebacks": "", "infrastructure_error": False},
    ]

    with (
        patch("backend.main.issue_service.get_issue", new_callable=AsyncMock) as mock_get_issue,
        patch("backend.main.planner_agent.create_plan", return_value={"steps": ["Plan"]}),
        patch("backend.main.coder_agent.generate_code", return_value="def fn(): pass\n"),
        patch("backend.main.coder_agent.improve_code", return_value="def fn(): pass\n"),
        patch("backend.main.reviewer_agent.review_code", return_value="VERDICT: APPROVE\nIssues: None\nFixes: None"),
        patch("backend.main.tester_agent.generate_tests", return_value="def test_fn(): pass"),
        patch("backend.main.generated_test_runner.run_generated_tests", side_effect=attempt_results),
    ):
        mock_get_issue.return_value = {"title": "Title", "body": "Body", "number": 1, "state": "open"}
        response = client.post("/agents/workflow", json={"repo": "om-3531/DevSquad", "issue_number": 1})

    assert response.status_code == 200
    data = response.json()
    # Latest attempt was TESTS_PASSED, not overridden by earlier attempt 1 or 2
    assert data["quality_gate"]["status"] == "REVIEW_APPROVED_GENERATED_TESTS_PASSED"
    assert data["test_execution"]["status"] == "PASSED"
    assert data["test_execution"]["summary"] == "attempt 3 passed completely"


# ==============================================================================
# Scenario 15: Workflow history preserves the correct stage, attempt count, and terminal status
# ==============================================================================
def test_scenario_15_workflow_history_preserves_stage_attempt_count_and_terminal_status(monkeypatch, tmp_path):
    wf_db = tmp_path / "test_wf_history.sqlite3"
    prop_db = tmp_path / "test_prop_history.sqlite3"
    wf_store = WorkflowStore(wf_db)
    prop_store = ProposalStore(prop_db)
    monkeypatch.setattr("backend.main.workflow_store", wf_store)
    monkeypatch.setattr("backend.main.proposal_store", prop_store)

    failing_result = {
        "status": "TESTS_FAILED",
        "passed": False,
        "summary": "Tests failed consistently",
        "return_code": 1,
        "failed_tests": ["test_create_issue_success_with_client_post"],
        "tracebacks": "Traceback info",
        "infrastructure_error": False,
    }

    with (
        patch("backend.main.issue_service.get_issue", new_callable=AsyncMock) as mock_get_issue,
        patch("backend.main.planner_agent.create_plan", return_value={"steps": ["Plan"]}),
        patch("backend.main.coder_agent.generate_code", return_value="def sample(): pass\n"),
        patch("backend.main.coder_agent.improve_code", return_value="def sample(): pass\n"),
        patch("backend.main.reviewer_agent.review_code", return_value="VERDICT: APPROVE\nIssues: None\nFixes: None"),
        patch("backend.main.tester_agent.generate_tests", return_value="def test_create_issue_success_with_client_post(): pass"),
        patch("backend.main.generated_test_runner.run_generated_tests", return_value=failing_result),
    ):
        mock_get_issue.return_value = {"title": "Issue", "body": "Body", "number": 1, "state": "open"}
        response = client.post("/agents/workflow", json={"repo": "om-3531/DevSquad", "issue_number": 1})

    assert response.status_code == 200, response.text
    wf_id = response.json()["workflow_id"]

    wf = wf_store.get_workflow(wf_id)
    assert wf is not None
    assert wf.status == STATUS_FAILED
    assert wf.stage == STAGE_TESTING
    assert "GENERATED_TESTS_FAILED" in wf.error_summary

    events = wf_store.get_workflow_events(wf_id)
    event_types = [e.event_type for e in events]
    assert EVENT_TESTS_FAILED in event_types
    assert EVENT_WORKFLOW_FAILED in event_types
    # Check that events record attempts
    coder_retry_events = [e for e in events if "Coder revising code after test failure" in e.message]
    assert len(coder_retry_events) == 2


# ==============================================================================
# Additional Safe Patch Validation Tests
# ==============================================================================
def test_patch_validation_rejects_unsafe_paths():
    valid, err = validate_target_path("../secrets.py", "modify")
    assert not valid
    assert "Directory traversal" in err

    valid, err = validate_target_path(".env", "modify")
    assert not valid
    assert "restricted" in err

    valid, err = validate_target_path("Dockerfile.test", "modify")
    assert not valid
    assert "restricted" in err


def test_patch_validation_detects_duplicate_component_implementation():
    base_code = (
        "from backend.github.client import GitHubClient\n"
        "from backend.github.validation import validate_repository\n\n"
        "class IssueService:\n"
        "    def __init__(self):\n"
        "        self.client = GitHubClient()\n"
    )
    # Candidate drops GitHubClient import and recreates dummy class
    candidate_code = (
        "class GitHubClient:\n"
        "    pass\n\n"
        "class IssueService:\n"
        "    def __init__(self):\n"
        "        self.client = GitHubClient()\n"
    )
    result = validate_candidate_patch(
        target_path="backend/github/issues.py",
        proposed_code=candidate_code,
        existing_code=base_code,
        operation="modify",
    )
    assert not result.is_valid
    assert any("recreates local definition" in issue for issue in result.issues)
    assert any("removes required architectural import" in issue for issue in result.issues)


@pytest.mark.asyncio
async def test_write_safety_guard_blocks_real_github_writes(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", False)
    service = IssueService()
    with pytest.raises(GitHubWritesDisabledError, match="writes are disabled"):
        await service.create_issue("om-3531/DevSquad", "Real Write Attempt")


def test_reviewer_accepts_corrected_issues_architecture():
    reviewer = ReviewerAgent()
    with open("backend/github/issues.py", "r", encoding="utf-8") as f:
        corrected_code = f.read()

    review = reviewer.review_code(
        task="Fix IssueService architecture to delegate to GitHubClient.create_issue",
        code=corrected_code,
        target_path="backend/github/issues.py",
        existing_code=corrected_code,
        operation="modify",
    )
    # The deterministic check should pass without finding architectural violations
    assert "Architectural preservation violation" not in review
    assert "httpx.AsyncClient" not in review


def test_reviewer_rejects_candidate_bypassing_client_with_httpx():
    reviewer = ReviewerAgent()
    bad_code = (
        "import httpx\n"
        "from backend.github.client import GitHubClient\n"
        "from backend.github.validation import validate_repository\n\n"
        "class IssueService:\n"
        "    def __init__(self, github_client=None):\n"
        "        self.github = github_client or GitHubClient()\n"
        "    async def get_issue(self, repo, issue_number):\n"
        "        return await self.github.get_issue(repo, issue_number)\n"
        "    async def create_issue(self, repo, title, body=None):\n"
        "        validate_repository(repo)\n"
        "        async with httpx.AsyncClient() as client:\n"
        "            resp = await client.post('https://api.github.com/repos/' + repo + '/issues', json={'title': title})\n"
        "            return resp.json()\n"
    )
    with open("backend/github/issues.py", "r", encoding="utf-8") as f:
        existing_code = f.read()

    review = reviewer.review_code(
        task="Update create_issue",
        code=bad_code,
        target_path="backend/github/issues.py",
        existing_code=existing_code,
        operation="modify",
    )
    assert "VERDICT: REQUEST_CHANGES" in review
    assert "httpx.AsyncClient" in review


def test_patch_validator_accepts_module_level_backend_import():
    base_code = (
        "from backend.github.client import GitHubClient\n"
        "from backend.github.validation import validate_repository\n\n"
        "class IssueService:\n"
        "    def __init__(self, github_client=None):\n"
        "        self.github = github_client or GitHubClient()\n"
        "    async def create_issue(self, repo, title, body=None):\n"
        "        validate_repository(repo)\n"
        "        return await self.github.create_issue(repo=repo, title=title, body=body)\n"
    )
    # Candidate imports module validation rather than importing symbol directly
    candidate_code = (
        "from backend.github import validation\n"
        "from backend.github.client import GitHubClient\n\n"
        "class IssueService:\n"
        "    def __init__(self, github_client=None):\n"
        "        self.github = github_client or GitHubClient()\n"
        "    async def create_issue(self, repo, title, body=None):\n"
        "        validation.validate_repository(repo)\n"
        "        return await self.github.create_issue(repo=repo, title=title, body=body)\n"
    )
    result = validate_candidate_patch(
        target_path="backend/github/issues.py",
        proposed_code=candidate_code,
        existing_code=base_code,
        operation="modify",
    )
    assert result.is_valid, f"Validation failed unexpectedly: {result.issues}"


def test_patch_validator_rejects_builtins_validate_repository():
    base_code = (
        "from backend.github.client import GitHubClient\n"
        "from backend.github.validation import validate_repository\n\n"
        "class IssueService:\n"
        "    def __init__(self, github_client=None):\n"
        "        self.github = github_client or GitHubClient()\n"
    )
    candidate_code = (
        "import builtins\n"
        "from backend.github.client import GitHubClient\n\n"
        "class IssueService:\n"
        "    def __init__(self, github_client=None):\n"
        "        self.github = github_client or GitHubClient()\n"
        "    async def create_issue(self, repo, title, body=None):\n"
        "        builtins.validate_repository(repo)\n"
        "        return await self.github.create_issue(repo=repo, title=title, body=body)\n"
    )
    result = validate_candidate_patch(
        target_path="backend/github/issues.py",
        proposed_code=candidate_code,
        existing_code=base_code,
        operation="modify",
    )
    assert not result.is_valid
    assert any("builtins.validate_repository" in issue for issue in result.issues)


def test_patch_validator_accepts_valid_issues_patch():
    with open("backend/github/issues.py", "r", encoding="utf-8") as f:
        valid_code = f.read()

    result = validate_candidate_patch(
        target_path="backend/github/issues.py",
        proposed_code=valid_code,
        existing_code=valid_code,
        operation="modify",
    )
    assert result.is_valid, f"Validation failed unexpectedly: {result.issues}"


def test_validate_repository_rejects_invalid_repos():
    from backend.github.validation import validate_repository
    for invalid in ["invalid_repo", "no-slash", "repo/", "/repo", "", "  ", "owner/repo/extra"]:
        with pytest.raises(ValueError):
            validate_repository(invalid)

    assert validate_repository("om-3531/DevSquad") == "om-3531/DevSquad"



