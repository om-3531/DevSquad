"""End-to-end verification script for Phase 2 critical flows.

Verifies:
A. Server and route availability.
B. Authenticated access to workflow history and statistics (and 401 when unauthenticated).
C. Workflow execution recording.
D. Persistence of execution events and results in SQLite.
E. Inspecting a failed workflow.
F. Requesting AI Failure Diagnosis and validating structure and content.
G. Testing deterministic fallback when AI provider is unavailable.
H. Generating a candidate fix proposal with diff preview requiring human approval.
I. Safe retry of eligible failed workflow with linkage to original.
J. Blocking non-retryable errors (e.g. 401 auth failures) and duplicate in-flight retries.
K. Preserving original execution history.
L. Confirming persisted data remains available across queries.
"""

import json
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings
from backend.workflows import (
    WorkflowStore,
    STATUS_FAILED,
    STATUS_RUNNING,
    STAGE_TESTING,
    STAGE_CODING,
    EVENT_WORKFLOW_STARTED,
    EVENT_TESTS_FAILED,
)
from backend.agents.diagnostician import DiagnosticAgent, CONFIDENCE_HIGH


@pytest.fixture
def viewer_token() -> str:
    data = json.loads(settings.APPROVAL_AUTH_TOKENS)
    for token, identity in data.items():
        if "workflow_reader" in identity.get("roles", []):
            return token
    pytest.skip("No workflow_reader token configured in test environment.")


@pytest.fixture
def approver_token() -> str:
    data = json.loads(settings.APPROVAL_AUTH_TOKENS)
    for token, identity in data.items():
        if "proposal_approver" in identity.get("roles", []):
            return token
    pytest.skip("No proposal_approver token configured in test environment.")


def test_phase2_flow_b_auth_and_stats(viewer_token: str):
    """Flow B: Verify authenticated access and unauthenticated rejection."""
    client = TestClient(app)

    # 1. Unauthenticated request to stats -> 401
    unauth_resp = client.get("/workflows/stats")
    assert unauth_resp.status_code == 401

    # 2. Authenticated request with viewer_token -> 200
    headers = {"Authorization": f"Bearer {viewer_token}"}
    auth_resp = client.get("/workflows/stats", headers=headers)
    assert auth_resp.status_code == 200
    data = auth_resp.json()
    assert data["status"] == "success"
    assert "stats" in data
    assert "total_executions" in data["stats"]
    assert "success_rate" in data["stats"]


def test_phase2_flows_c_d_e_persistence_and_inspection(viewer_token: str, tmp_path):
    """Flows C, D, E: Workflow recording, persistence in SQLite, inspecting failed run."""
    db_file = tmp_path / "test_phase2.sqlite3"
    wf_store = WorkflowStore(str(db_file))

    # Create and fail workflow execution
    wf = wf_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=42,
    )
    wf_store.update_workflow(
        wf.workflow_id,
        status=STATUS_FAILED,
        stage=STAGE_TESTING,
        error_summary="Docker container engine unreachable: Test runner failed.",
    )
    wf_store.record_event(
        wf.workflow_id,
        event_type=EVENT_WORKFLOW_STARTED,
        stage=STAGE_TESTING,
        message="Workflow execution started.",
    )
    wf_store.record_event(
        wf.workflow_id,
        event_type=EVENT_TESTS_FAILED,
        stage=STAGE_TESTING,
        message="Docker container engine unreachable: Test runner failed.",
    )

    # Verify persistence in SQLite
    reloaded_store = WorkflowStore(str(db_file))
    fetched = reloaded_store.get_workflow(wf.workflow_id)
    assert fetched is not None
    assert fetched.status == STATUS_FAILED
    assert fetched.stage == STAGE_TESTING
    assert "Docker" in fetched.error_summary

    events = reloaded_store.get_workflow_events(wf.workflow_id)
    assert len(events) == 2
    assert events[1].event_type == EVENT_TESTS_FAILED


def test_phase2_flows_f_g_diagnosis_and_fallback(viewer_token: str):
    """Flows F, G: Diagnosis structure and offline fallback."""
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {viewer_token}"}

    # Test G: Fallback when LLM client is None (offline or error)
    agent = DiagnosticAgent(client=None)
    fallback = agent.synthesize_fallback_diagnosis(
        stage="TESTING",
        error_summary="Docker container engine unreachable: sandbox execution failed",
        agent_statuses={"planner": "COMPLETED", "coder": "COMPLETED", "tester": "FAILED", "reviewer": "COMPLETED"},
        events=[{"event_type": "TESTS_FAILED", "stage": "TESTING", "message": "Docker engine socket unreachable"}],
        repository="om-3531/DevSquad",
        issue_number=1,
    )
    assert fallback["confidence"] == CONFIDENCE_HIGH
    assert fallback["failed_stage"] == "TESTING"
    assert "Docker" in fallback["probable_root_cause"]
    assert len(fallback["supporting_evidence"]) > 0
    assert len(fallback["recommended_steps"]) > 0

    # Test F: Request diagnosis via API on a failed workflow
    from backend.main import workflow_store
    wf = workflow_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=99,
    )
    workflow_store.update_workflow(
        wf.workflow_id,
        status=STATUS_FAILED,
        stage=STAGE_CODING,
        error_summary="SyntaxError in generated candidate: invalid syntax at line 14",
    )
    workflow_store.record_event(
        wf.workflow_id,
        event_type="CODE_GENERATION_FAILED",
        stage=STAGE_CODING,
        message="SyntaxError in generated candidate: invalid syntax at line 14",
    )

    diag_resp = client.post(f"/workflows/{wf.workflow_id}/diagnose", headers=headers, json={"force_refresh": True})
    assert diag_resp.status_code == 200
    diag_data = diag_resp.json()
    assert diag_data["status"] == "success"
    assert "diagnosis" in diag_data
    d = diag_data["diagnosis"]
    assert d["failed_stage"] == STAGE_CODING
    assert "summary" in d
    assert "probable_root_cause" in d
    assert isinstance(d["supporting_evidence"], list)
    assert isinstance(d["recommended_steps"], list)
    assert d["confidence"] in ["HIGH", "MEDIUM", "LOW"]


def test_phase2_flow_h_fix_proposal_and_approval(approver_token: str):
    """Flow H: Fix proposal generation, diff preview, explicit approval requirement."""
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {approver_token}"}

    from backend.main import workflow_store
    wf = workflow_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=101,
        metadata={"target_path": "calc.py"},
    )
    workflow_store.update_workflow(
        wf.workflow_id,
        status=STATUS_FAILED,
        stage=STAGE_TESTING,
        error_summary="AssertionError in test_calc",
    )
    workflow_store.store_diagnosis(
        wf.workflow_id,
        {
            "summary": "Fix calc off-by-one error",
            "failed_stage": STAGE_TESTING,
            "probable_root_cause": "Calculation returns x rather than x + 1",
            "supporting_evidence": ["test_calc failed"],
            "recommended_steps": ["Update calc.py"],
            "suggested_fix": "def add_one(x):\n    return x + 1\n",
            "confidence": "HIGH",
        },
    )

    with (
        patch("backend.main.repository_service.get_repository", new_callable=AsyncMock, return_value={"default_branch": "main"}),
        patch("backend.main.repository_service.get_branch_sha", new_callable=AsyncMock, return_value="a" * 40),
        patch("backend.main.repository_service.get_file", new_callable=AsyncMock, return_value={"decoded_content": "def add_one(x):\n    return x\n", "sha": "b" * 40}),
    ):
        resp = client.post(f"/workflows/{wf.workflow_id}/fix-proposal", headers=headers)

    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["status"] == "success"
    proposal = res_data["proposal"]
    diff_preview = res_data["diff_preview"]

    # Diff preview must be visible
    assert diff_preview["path"] == "calc.py"
    assert diff_preview["changed"] is True
    assert "+    return x + 1" in diff_preview["diff"]

    # Explicit human approval requirement
    assert proposal["status"] in ["PENDING_REVIEW", "NOT_ELIGIBLE"]
    assert proposal["approved_at"] is None
    assert proposal["commit_sha"] is None


def test_phase2_flows_i_j_k_l_safe_retry_and_linkage(viewer_token: str):
    """Flows I, J, K, L: Safe retry, linkage, deduplication, non-retryable blocks, history preservation."""
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {viewer_token}"}

    from backend.main import workflow_store

    # Flow J: Non-retryable error (e.g. 401 auth error)
    auth_fail_wf = workflow_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=1,
    )
    workflow_store.update_workflow(
        auth_fail_wf.workflow_id,
        status=STATUS_FAILED,
        stage=STAGE_CODING,
        error_summary="HTTP 401 Unauthorized: Invalid GitHub token credentials",
    )
    resp_non_retryable = client.post(f"/workflows/{auth_fail_wf.workflow_id}/retry", headers=headers)
    assert resp_non_retryable.status_code == 400
    assert "authentication error" in resp_non_retryable.json()["detail"].lower()

    # Flow J: Deduplication - cannot retry if a retry is currently RUNNING
    parent_wf = workflow_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=2,
    )
    workflow_store.update_workflow(
        parent_wf.workflow_id,
        status=STATUS_FAILED,
        stage=STAGE_TESTING,
        error_summary="Transient timeout during test run",
    )
    in_flight_retry = workflow_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=2,
        status=STATUS_RUNNING,
        stage=STAGE_TESTING,
        retry_of_workflow_id=parent_wf.workflow_id,
    )
    resp_duplicate = client.post(f"/workflows/{parent_wf.workflow_id}/retry", headers=headers)
    assert resp_duplicate.status_code == 409
    assert "already in progress" in resp_duplicate.json()["detail"]

    # Flow I & K: Successful retry execution and linkage
    workflow_store.update_workflow(in_flight_retry.workflow_id, status=STATUS_FAILED)
    with patch("backend.main.execute_agent_workflow_internal", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {
            "workflow_id": "new-retry-exec-uuid",
            "retry_of_workflow_id": parent_wf.workflow_id,
            "status": "success",
        }
        resp_retry = client.post(f"/workflows/{parent_wf.workflow_id}/retry", headers=headers)

    assert resp_retry.status_code == 200
    retry_result = resp_retry.json()
    assert retry_result["status"] == "success"
    assert retry_result["retry_of_workflow_id"] == parent_wf.workflow_id

    # Flow K: Original execution preserved
    original_after = workflow_store.get_workflow(parent_wf.workflow_id)
    assert original_after.status == STATUS_FAILED
    assert original_after.workflow_id == parent_wf.workflow_id

    # Flow L: Refresh/query history returns persisted data
    wf_list, total = workflow_store.list_workflows()
    assert total >= 3
    found_parent = next((w for w in wf_list if w.workflow_id == parent_wf.workflow_id), None)
    assert found_parent is not None
