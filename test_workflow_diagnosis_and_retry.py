"""Tests for AI Failure Diagnosis and Safe Workflow Retry."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.agents.diagnostician import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    DiagnosticAgent,
)
from backend.auth import (
    AuthenticatedIdentity,
    PROPOSAL_APPROVER,
    PROPOSAL_READER,
    WORKFLOW_READER,
)
from backend.main import app
from backend.proposals import ProposalStore
from backend.workflows import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    STAGE_INITIALIZING,
    STAGE_PLANNING,
    STAGE_CODING,
    STAGE_TESTING,
    STAGE_FAILED,
    EVENT_WORKFLOW_STARTED,
    EVENT_WORKFLOW_FAILED,
    EVENT_DIAGNOSIS_REQUESTED,
    EVENT_DIAGNOSIS_COMPLETED,
    EVENT_WORKFLOW_RETRIED,
    WorkflowStore,
)

READER_TOKEN = "devsquad-test-reader-token-1111111111111111"
WORKFLOW_READER_TOKEN = "devsquad-test-wf-reader-token-55555555555"
UNAUTHORIZED_TOKEN = "devsquad-test-unauth-token-99999999999"

TEST_TOKENS_CONFIG = {
    READER_TOKEN: {
        "subject": "reader@devsquad.local",
        "roles": [PROPOSAL_READER],
    },
    WORKFLOW_READER_TOKEN: {
        "subject": "wfreader@devsquad.local",
        "roles": [WORKFLOW_READER],
    },
    UNAUTHORIZED_TOKEN: {
        "subject": "unauth@devsquad.local",
        "roles": ["guest"],
    },
}


@pytest.fixture
def isolated_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_diagnosis_retry.sqlite3"


@pytest.fixture
def isolated_proposal_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_proposals.sqlite3"


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch, isolated_db_path: Path, isolated_proposal_db_path: Path):
    monkeypatch.setenv("APPROVAL_AUTH_TOKENS", json.dumps(TEST_TOKENS_CONFIG))
    test_wf_store = WorkflowStore(isolated_db_path)
    test_prop_store = ProposalStore(isolated_proposal_db_path)
    monkeypatch.setattr("backend.main.workflow_store", test_wf_store)
    monkeypatch.setattr("backend.main.proposal_store", test_prop_store)
    client = TestClient(app)
    return client, test_wf_store, test_prop_store


def test_diagnostician_rule_based_fallback():
    """Verify deterministic fallback synthesizer when LLM is offline or unconfigured."""
    agent = DiagnosticAgent(client=None)

    # 1. Docker failure
    diag_docker = agent.synthesize_fallback_diagnosis(
        stage="TESTING",
        error_summary="Test environment error: Docker Desktop engine is not running",
        agent_statuses={"tester": "FAILED"},
        events=[{"stage": "TESTING", "event_type": "TESTS_FAILED", "message": "Docker engine unreachable"}],
        repository="om-3531/DevSquad",
        issue_number=1,
    )
    assert diag_docker["confidence"] == CONFIDENCE_HIGH
    assert "docker" in diag_docker["probable_root_cause"].lower()
    assert any("Docker" in s for s in diag_docker["recommended_steps"])

    # 2. Rate limit failure
    diag_rl = agent.synthesize_fallback_diagnosis(
        stage="CODING",
        error_summary="RateLimitError: 429 rate limit exceeded",
        agent_statuses={"coder": "FAILED"},
        events=[],
        repository="om-3531/DevSquad",
        issue_number=1,
    )
    assert diag_rl["confidence"] == CONFIDENCE_HIGH
    assert "rate limit" in diag_rl["probable_root_cause"].lower()

    # 3. Missing error and events
    diag_empty = agent.synthesize_fallback_diagnosis(
        stage="INITIALIZING",
        error_summary=None,
        agent_statuses={},
        events=[],
        repository="om-3531/DevSquad",
        issue_number=1,
    )
    assert diag_empty["confidence"] == CONFIDENCE_LOW
    assert "insufficient" in diag_empty["probable_root_cause"].lower()


def test_diagnostician_llm_flow_and_guardrails():
    """Verify that DiagnosticAgent parses structured LLM output and sanitizes secrets."""
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({
        "summary": "Coder produced syntax error in solution.py with secret gsk_1234567890abcdef",
        "failed_stage": "CODING",
        "probable_root_cause": "Unterminated string literal in generated function",
        "supporting_evidence": ["SyntaxError on line 12", "Bearer secret_token_123456789"],
        "recommended_steps": ["Fix string quotation", "Re-run coder agent"],
        "suggested_fix": "def add(a, b):\n    return a + b\n",
        "confidence": "HIGH",
    })
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    agent = DiagnosticAgent(client=mock_client)
    res = agent.diagnose(
        stage="CODING",
        error_summary="SyntaxError: unterminated string literal",
        agent_statuses={"coder": "FAILED"},
        events=[],
        repository="om-3531/DevSquad",
        issue_number=42,
    )

    assert res["confidence"] == CONFIDENCE_HIGH
    assert res["failed_stage"] == "CODING"
    assert res["suggested_fix"] == "def add(a, b):\n    return a + b\n"
    # Verify secrets are scrubbed
    assert "gsk_1234567890abcdef" not in res["summary"]
    assert "[REDACTED" in res["summary"]
    assert "secret_token_123456789" not in str(res["supporting_evidence"])


def test_diagnose_api_success_and_caching(auth_client, monkeypatch):
    client, wf_store, _ = auth_client

    # Create failed workflow
    wf = wf_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=10,
        workflow_id="wf-failed-1",
        status=STATUS_FAILED,
        stage=STAGE_TESTING,
    )
    wf_store.update_workflow(
        "wf-failed-1",
        error_summary="Quality gate not met: GENERATED_TESTS_FAILED",
        tester_status="FAILED",
    )
    wf_store.record_event(
        "wf-failed-1",
        event_type=EVENT_WORKFLOW_FAILED,
        stage=STAGE_TESTING,
        message="Quality gate failure during tests",
    )

    headers = {"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"}

    # 1. First diagnosis request (computes and caches)
    resp = client.post("/workflows/wf-failed-1/diagnose", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["cached"] is False
    diag = data["diagnosis"]
    assert diag["failed_stage"] == STAGE_TESTING
    assert diag["confidence"] in {CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW}

    # 2. Second diagnosis request without force_refresh (returns cached)
    resp2 = client.post("/workflows/wf-failed-1/diagnose", headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["cached"] is True
    assert data2["diagnosis"]["summary"] == diag["summary"]

    # 3. Third diagnosis request with force_refresh=True
    resp3 = client.post("/workflows/wf-failed-1/diagnose", json={"force_refresh": True}, headers=headers)
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert data3["cached"] is False


def test_diagnose_api_non_failed_and_not_found(auth_client):
    client, wf_store, _ = auth_client
    headers = {"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"}

    # 1. 404 for nonexistent workflow
    resp = client.post("/workflows/wf-nonexistent/diagnose", headers=headers)
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()

    # 2. Successful workflow returns clear response stating no failure
    wf_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=12,
        workflow_id="wf-success-1",
        status=STATUS_SUCCESS,
        stage="COMPLETED",
    )
    resp_success = client.post("/workflows/wf-success-1/diagnose", headers=headers)
    assert resp_success.status_code == 200
    data = resp_success.json()
    assert "not recorded any failure" in data["diagnosis"]["summary"]


def test_diagnose_api_auth_requirements(auth_client):
    client, wf_store, _ = auth_client
    wf_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=1,
        workflow_id="wf-auth-test",
        status=STATUS_FAILED,
        stage=STAGE_FAILED,
    )

    # 1. No auth token -> 401
    resp_no_auth = client.post("/workflows/wf-auth-test/diagnose")
    assert resp_no_auth.status_code == 401

    # 2. Invalid token -> 401
    resp_bad = client.post("/workflows/wf-auth-test/diagnose", headers={"Authorization": "Bearer bad-token"})
    assert resp_bad.status_code == 401

    # 3. Unauthorized role -> 403
    resp_unauth = client.post(
        "/workflows/wf-auth-test/diagnose",
        headers={"Authorization": f"Bearer {UNAUTHORIZED_TOKEN}"},
    )
    assert resp_unauth.status_code == 403


def test_retry_workflow_eligibility_and_deduplication(auth_client, monkeypatch):
    client, wf_store, _ = auth_client
    headers = {"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"}

    # 1. Cannot retry nonexistent workflow -> 404
    r1 = client.post("/workflows/wf-missing/retry", headers=headers)
    assert r1.status_code == 404

    # 2. Cannot retry non-failed workflow -> 400
    wf_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=5,
        workflow_id="wf-running-1",
        status=STATUS_RUNNING,
        stage=STAGE_CODING,
    )
    r2 = client.post("/workflows/wf-running-1/retry", headers=headers)
    assert r2.status_code == 400
    assert "Only failed workflows" in r2.json()["detail"]

    # 3. Cannot retry non-retryable authentication failure -> 400
    wf_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=5,
        workflow_id="wf-auth-failed",
        status=STATUS_FAILED,
        stage=STAGE_INITIALIZING,
    )
    wf_store.update_workflow("wf-auth-failed", error_summary="401: GitHub authentication failed. Check GITHUB_TOKEN.")
    r3 = client.post("/workflows/wf-auth-failed/retry", headers=headers)
    assert r3.status_code == 400
    assert "authentication error" in r3.json()["detail"].lower()

    # 4. In-flight deduplication: Cannot retry if a retry is currently RUNNING -> 409
    wf_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=5,
        workflow_id="wf-orig-failed",
        status=STATUS_FAILED,
        stage=STAGE_TESTING,
    )
    wf_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=5,
        workflow_id="wf-child-retry-1",
        status=STATUS_RUNNING,
        stage=STAGE_PLANNING,
        retry_of_workflow_id="wf-orig-failed",
    )
    r4 = client.post("/workflows/wf-orig-failed/retry", headers=headers)
    assert r4.status_code == 409
    assert "already in progress" in r4.json()["detail"].lower()


@pytest.mark.asyncio
async def test_retry_workflow_success_linkage(auth_client, monkeypatch):
    client, wf_store, _ = auth_client
    headers = {"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"}

    # Create failed workflow
    wf_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=42,
        workflow_id="wf-to-retry",
        status=STATUS_FAILED,
        stage=STAGE_TESTING,
        metadata={"target_path": "calculator.py"},
    )
    wf_store.update_workflow("wf-to-retry", error_summary="Quality gate not met: GENERATED_TESTS_FAILED")

    # Mock execute_agent_workflow_internal to simulate successful rerun
    async def mock_execute(request, retry_of_workflow_id=None):
        new_wf_id = "wf-retry-new-123"
        wf_store.create_workflow(
            workflow_id=new_wf_id,
            repository=request.repo,
            issue_number=request.issue_number,
            status=STATUS_SUCCESS,
            stage="COMPLETED",
            retry_of_workflow_id=retry_of_workflow_id,
        )
        return {
            "status": "success",
            "workflow_id": new_wf_id,
            "retry_of_workflow_id": retry_of_workflow_id,
            "issue_number": request.issue_number,
            "quality_gate": {"approved": True, "status": "REVIEW_APPROVED_GENERATED_TESTS_PASSED"},
        }

    monkeypatch.setattr("backend.main.execute_agent_workflow_internal", mock_execute)

    resp = client.post("/workflows/wf-to-retry/retry", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["retry_of_workflow_id"] == "wf-to-retry"
    assert data["workflow_id"] == "wf-retry-new-123"

    # Verify original workflow history preserved intact
    orig = wf_store.get_workflow("wf-to-retry")
    assert orig.status == STATUS_FAILED
    events = wf_store.get_workflow_events("wf-to-retry")
    assert any(e.event_type == EVENT_WORKFLOW_RETRIED for e in events)

    # Verify retry record in database links to original
    child = wf_store.get_workflow("wf-retry-new-123")
    assert child is not None
    assert child.retry_of_workflow_id == "wf-to-retry"


@pytest.mark.asyncio
async def test_create_fix_proposal_requires_approval(auth_client, monkeypatch):
    client, wf_store, prop_store = auth_client
    headers = {"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"}

    wf = wf_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=8,
        workflow_id="wf-diagnosed-fix",
        status=STATUS_FAILED,
        stage=STAGE_CODING,
        metadata={"target_path": "math_utils.py"},
    )
    # Store diagnosis with suggested fix
    wf_store.store_diagnosis(
        "wf-diagnosed-fix",
        {
            "summary": "Syntax error in math_utils.py",
            "failed_stage": "CODING",
            "probable_root_cause": "Missing colon in def statement",
            "supporting_evidence": ["Line 4: SyntaxError"],
            "recommended_steps": ["Add missing colon"],
            "suggested_fix": "def add(x, y):\n    return x + y\n",
            "confidence": "HIGH",
        },
    )

    # Mock repository service methods
    async def mock_get_repo(repo):
        return {"default_branch": "main"}

    async def mock_get_sha(repo, branch):
        return "a" * 40

    async def mock_get_file(repo, path, branch):
        return {"decoded_content": "def add(x, y)\n    return x + y\n", "sha": "b" * 40}

    monkeypatch.setattr("backend.main.repository_service.get_repository", mock_get_repo)
    monkeypatch.setattr("backend.main.repository_service.get_branch_sha", mock_get_sha)
    monkeypatch.setattr("backend.main.repository_service.get_file", mock_get_file)

    resp = client.post("/workflows/wf-diagnosed-fix/fix-proposal", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "Candidate patch created as a pending proposal" in data["message"]
    proposal_id = data["proposal_id"]

    # Verify proposal is created as a candidate requiring review (never written silently)
    proposal = prop_store.get(proposal_id)
    assert proposal is not None
    assert proposal.status in {"PENDING_APPROVAL", "NOT_ELIGIBLE", "PENDING_REVIEW"}
    assert proposal.quality_gate_status == "DIAGNOSTIC_FIX_SUGGESTION"
    assert proposal.proposed_content.strip() == "def add(x, y):\n    return x + y"
