"""
Comprehensive End-to-End Feature Audit Test Suite for DevSquad.
Verifies all existing endpoints, services, database interactions, auth, AI agents,
proposal cryptographic gating, and safety mechanisms.
"""

from __future__ import annotations

import gc
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from backend.config import settings
settings.reload(override=True)

from backend.auth import (
    ApprovalRateLimiter,
    AuthenticatedIdentity,
    BearerTokenAuthenticator,
    PROPOSAL_APPROVER,
    PROPOSAL_READER,
    WORKFLOW_READER,
)
from backend.main import (
    app,
    approval_rate_limiter,
    proposal_store as main_proposal_store,
    workflow_store as main_workflow_store,
)
from backend.proposals import (
    ProposalStore,
    PENDING_REVIEW,
    APPROVED,
    REJECTED,
    NOT_ELIGIBLE,
    PR_CREATED,
    WRITE_FAILED,
)
from backend.workflows import (
    WorkflowStore,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_CANCELLED,
    STAGE_INITIALIZING,
    STAGE_PLANNING,
    STAGE_CODING,
    STAGE_TESTING,
    STAGE_REVIEWING,
    STAGE_COMPLETED,
    STAGE_FAILED,
)
from backend.diff_preview import build_diff_preview

READER_TOKEN = "devsquad-audit-reader-token-1111111111111111"
APPROVER_TOKEN = "devsquad-audit-approver-token-2222222222222222"
WORKFLOW_READER_TOKEN = "devsquad-audit-wf-reader-token-55555555555"

TEST_TOKENS_CONFIG = {
    READER_TOKEN: {
        "subject": "reader@devsquad.local",
        "roles": [PROPOSAL_READER],
    },
    APPROVER_TOKEN: {
        "subject": "approver@devsquad.local",
        "roles": [PROPOSAL_READER, PROPOSAL_APPROVER, WORKFLOW_READER],
    },
    WORKFLOW_READER_TOKEN: {
        "subject": "wfreader@devsquad.local",
        "roles": [WORKFLOW_READER],
    },
}

test_db_dir = Path("tests/scratch_audit")
test_db_dir.mkdir(parents=True, exist_ok=True)
prop_db = test_db_dir / "audit_proposals.sqlite3"
wf_db = test_db_dir / "audit_workflows.sqlite3"

# Initialize isolated stores
test_prop_store = ProposalStore(prop_db)
test_wf_store = WorkflowStore(wf_db)

client = TestClient(app)


def test_feature_1_root_and_health():
    """Feature 1: Root and health endpoints."""
    res_root = client.get("/")
    assert res_root.status_code == 200
    data_root = res_root.json()
    assert data_root["project"] == "DevSquad"
    assert "version" in data_root

    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json() == {"status": "healthy"}


def test_feature_2_models_status():
    """Feature 2: AI model availability and fallback status."""
    res = client.get("/models/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "current_model" in data
    assert "configured_fallback" in data


def test_feature_3_github_repository_endpoint():
    """Feature 3: GitHub repository metadata retrieval and validation."""
    # Positive case: valid repository pattern
    with patch("backend.main.repository_service.get_repository", new_callable=AsyncMock) as mock_get_repo:
        mock_get_repo.return_value = {
            "name": "devsquad-test",
            "owner": "om-3531",
            "full_name": "om-3531/devsquad-test",
            "default_branch": "main",
        }
        res = client.get("/github/repository?repo=om-3531/devsquad-test")
        assert res.status_code == 200
        assert res.json()["repository"]["default_branch"] == "main"

    # Negative case: invalid repository format (fails regex pattern)
    res_bad = client.get("/github/repository?repo=invalid-format")
    assert res_bad.status_code == 422


def test_feature_4_github_issue_endpoint():
    """Feature 4: GitHub issue details retrieval and validation."""
    with patch("backend.main.issue_service.get_issue", new_callable=AsyncMock) as mock_get_issue:
        mock_get_issue.return_value = {
            "number": 1,
            "title": "Add is_even utility",
            "body": "Implement a helper function is_even(n: int) -> bool.",
        }
        res = client.post("/github/issue", json={"repo": "om-3531/devsquad-test", "issue_number": 1})
        assert res.status_code == 200
        assert res.json()["issue"]["title"] == "Add is_even utility"

    # Negative: invalid issue number
    res_bad = client.post("/github/issue", json={"repo": "om-3531/devsquad-test", "issue_number": -5})
    assert res_bad.status_code == 422


def test_feature_5_agent_review_endpoint():
    """Feature 5: Reviewer agent individual endpoint."""
    # Positive case
    with patch("backend.main.reviewer_agent.review_code") as mock_rev:
        mock_rev.return_value = {
            "verdict": "APPROVED",
            "summary": "Implementation looks clean and correct.",
            "issues": [],
            "feedback": "No changes requested.",
        }
        res = client.post("/agents/review", json={"task": "Implement is_even", "code": "def is_even(n: int) -> bool:\n    return n % 2 == 0\n"})
        assert res.status_code == 200
        assert res.json()["review"]["verdict"] == "APPROVED"

    # Negative case: missing task or code
    res_missing = client.post("/agents/review", json={"task": "", "code": "x = 1"})
    assert res_missing.status_code == 400


def test_feature_6_proposal_lifecycle_and_security():
    """Feature 6: Proposal creation, get, audit, approval, rejection, and tamper security."""
    diff_preview = build_diff_preview(
        path="utils/helper.py",
        operation="create",
        proposed_content="def helper(): return True\n",
        original_content="",
        original_content_available=True,
    )
    prop = test_prop_store.create(
        repository="om-3531/devsquad-test",
        issue_number=1,
        target_branch="main",
        target_path="utils/helper.py",
        operation="create",
        proposed_content="def helper(): return True\n",
        diff_preview=diff_preview,
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        base_commit_sha="a" * 40,
        target_file_blob_sha=None,
    )

    with patch("backend.main.proposal_store", test_prop_store), \
         patch("backend.main.get_approval_authenticator", return_value=BearerTokenAuthenticator.from_json(json.dumps(TEST_TOKENS_CONFIG))):

        # 1. Unauthenticated read -> 401
        res_no_auth = client.get(f"/proposals/{prop.proposal_id}")
        assert res_no_auth.status_code == 401

        # 2. Authenticated read -> 200
        res_auth = client.get(
            f"/proposals/{prop.proposal_id}",
            headers={"Authorization": f"Bearer {READER_TOKEN}"},
        )
        assert res_auth.status_code == 200
        assert res_auth.json()["proposal"]["proposal_id"] == prop.proposal_id

        # 3. Audit trail read -> 200
        res_audit = client.get(
            f"/proposals/{prop.proposal_id}/audit",
            headers={"Authorization": f"Bearer {READER_TOKEN}"},
        )
        assert res_audit.status_code == 200
        assert "audit_events" in res_audit.json()

        # 4. Approval with insufficient role (reader only) -> 403
        digest = ProposalStore.digest("def helper(): return True\n")
        res_forbidden = client.post(
            f"/proposals/{prop.proposal_id}/approve",
            headers={"Authorization": f"Bearer {READER_TOKEN}"},
            json={"content_digest": digest},
        )
        assert res_forbidden.status_code == 403

        # 5. Approval with mismatched digest (tampering detected) -> 409
        bad_digest = ProposalStore.digest("tampered content")
        res_tamper = client.post(
            f"/proposals/{prop.proposal_id}/approve",
            headers={"Authorization": f"Bearer {APPROVER_TOKEN}"},
            json={"content_digest": bad_digest},
        )
        assert res_tamper.status_code == 409
        assert "does not match" in res_tamper.json()["detail"]

        # 6. Valid approval by authorized approver -> 200
        res_approve = client.post(
            f"/proposals/{prop.proposal_id}/approve",
            headers={"Authorization": f"Bearer {APPROVER_TOKEN}"},
            json={"content_digest": digest},
        )
        assert res_approve.status_code == 200
        assert res_approve.json()["proposal"]["status"] == APPROVED

        # 7. Re-approval of already approved -> 409
        res_reapprove = client.post(
            f"/proposals/{prop.proposal_id}/approve",
            headers={"Authorization": f"Bearer {APPROVER_TOKEN}"},
            json={"content_digest": digest},
        )
        assert res_reapprove.status_code == 409


def test_feature_7_proposal_rejection():
    """Feature 7: Proposal rejection and terminal state locking."""
    diff_preview = build_diff_preview(
        path="utils/helper2.py",
        operation="create",
        proposed_content="def helper2(): return False\n",
        original_content="",
        original_content_available=True,
    )
    prop = test_prop_store.create(
        repository="om-3531/devsquad-test",
        issue_number=2,
        target_branch="main",
        target_path="utils/helper2.py",
        operation="create",
        proposed_content="def helper2(): return False\n",
        diff_preview=diff_preview,
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        base_commit_sha="b" * 40,
        target_file_blob_sha=None,
    )

    with patch("backend.main.proposal_store", test_prop_store), \
         patch("backend.main.get_approval_authenticator", return_value=BearerTokenAuthenticator.from_json(json.dumps(TEST_TOKENS_CONFIG))):

        # Rejection by approver
        res_reject = client.post(
            f"/proposals/{prop.proposal_id}/reject",
            headers={"Authorization": f"Bearer {APPROVER_TOKEN}"},
            json={"reason": "Flawed design"},
        )
        assert res_reject.status_code == 200
        assert res_reject.json()["proposal"]["status"] == REJECTED

        # Subsequent approval blocked
        digest = ProposalStore.digest("def helper2(): return False\n")
        res_approve_blocked = client.post(
            f"/proposals/{prop.proposal_id}/approve",
            headers={"Authorization": f"Bearer {APPROVER_TOKEN}"},
            json={"content_digest": digest},
        )
        assert res_approve_blocked.status_code == 409


def test_feature_8_write_orchestrator_safety_gate():
    """Feature 8: GitHub write execution strictly blocked when writes are disabled."""
    diff_preview = build_diff_preview(
        path="utils/helper3.py",
        operation="create",
        proposed_content="x = 42\n",
        original_content="",
        original_content_available=True,
    )
    prop = test_prop_store.create(
        repository="om-3531/devsquad-test",
        issue_number=3,
        target_branch="main",
        target_path="utils/helper3.py",
        operation="create",
        proposed_content="x = 42\n",
        diff_preview=diff_preview,
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        base_commit_sha="c" * 40,
        target_file_blob_sha=None,
    )
    digest = ProposalStore.digest("x = 42\n")
    test_prop_store.approve(prop.proposal_id, digest, approved_by="admin")

    with patch("backend.main.proposal_store", test_prop_store), \
         patch("backend.main.get_approval_authenticator", return_value=BearerTokenAuthenticator.from_json(json.dumps(TEST_TOKENS_CONFIG))):

        assert settings.GITHUB_WRITES_ENABLED is False
        res_write = client.post(
            f"/proposals/{prop.proposal_id}/write",
            headers={"Authorization": f"Bearer {APPROVER_TOKEN}"},
        )
        # Must return 503 Service Unavailable with safe error message
        assert res_write.status_code == 503
        assert "GitHub writes are disabled" in res_write.json()["detail"]


def test_feature_9_workflow_history_and_stats():
    """Feature 9: Workflow listing, filtering, pagination, stats, and event tracking."""
    # Populate a test workflow
    wf = test_wf_store.create_workflow(
        repository="om-3531/devsquad-test",
        issue_number=10,
        metadata={"target_path": "math/calc.py"},
    )
    wf_id = wf.workflow_id
    test_wf_store.update_workflow(
        wf_id,
        status=STATUS_SUCCESS,
        stage=STAGE_COMPLETED,
        planner_status="COMPLETED",
        coder_status="COMPLETED",
        tester_status="COMPLETED",
        reviewer_status="COMPLETED",
    )
    test_wf_store.record_event(
        wf_id,
        event_type="WORKFLOW_STARTED",
        stage=STAGE_INITIALIZING,
        message="Started",
    )
    test_wf_store.record_event(
        wf_id,
        event_type="WORKFLOW_SUCCEEDED",
        stage=STAGE_COMPLETED,
        message="Workflow succeeded",
    )

    with patch("backend.main.workflow_store", test_wf_store), \
         patch("backend.main.get_approval_authenticator", return_value=BearerTokenAuthenticator.from_json(json.dumps(TEST_TOKENS_CONFIG))):

        # 1. Stats endpoint
        res_stats = client.get(
            "/workflows/stats",
            headers={"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"},
        )
        assert res_stats.status_code == 200
        stats = res_stats.json()["stats"]
        assert stats["total_executions"] >= 1
        assert "success_rate" in stats

        # 2. Workflows listing
        res_list = client.get(
            "/workflows?status=SUCCESS&limit=10&offset=0",
            headers={"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"},
        )
        assert res_list.status_code == 200
        data_list = res_list.json()
        assert data_list["total"] >= 1
        assert len(data_list["workflows"]) >= 1

        # 3. Invalid status filter -> 400
        res_bad_status = client.get(
            "/workflows?status=BOGUS_STATUS",
            headers={"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"},
        )
        assert res_bad_status.status_code == 400

        # 4. Single workflow details
        res_detail = client.get(
            f"/workflows/{wf_id}",
            headers={"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"},
        )
        assert res_detail.status_code == 200
        assert res_detail.json()["workflow"]["workflow_id"] == wf_id

        # 5. Workflow events
        res_events = client.get(
            f"/workflows/{wf_id}/events",
            headers={"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"},
        )
        assert res_events.status_code == 200
        events = res_events.json()["events"]
        assert len(events) >= 2


def test_feature_10_diagnosis_retry_and_fix_proposal():
    """Feature 10: Workflow diagnosis, retry safeguards, and fix proposal creation."""
    # Create a failed workflow
    wf = test_wf_store.create_workflow(
        repository="om-3531/devsquad-test",
        issue_number=11,
        metadata={"target_path": "math/add.py"},
    )
    wf_id = wf.workflow_id
    test_wf_store.update_workflow(
        wf_id,
        status=STATUS_FAILED,
        stage=STAGE_TESTING,
        error_summary="AssertionError: assert add(2, 3) == 5 failed, got 6",
    )

    with patch("backend.main.workflow_store", test_wf_store), \
         patch("backend.main.proposal_store", test_prop_store), \
         patch("backend.main.get_approval_authenticator", return_value=BearerTokenAuthenticator.from_json(json.dumps(TEST_TOKENS_CONFIG))), \
         patch("backend.main.diagnostic_agent.diagnose") as mock_diag, \
         patch("backend.main.repository_service.get_repository", new_callable=AsyncMock) as mock_get_repo, \
         patch("backend.main.repository_service.get_branch_sha", new_callable=AsyncMock) as mock_sha:

        mock_diag.return_value = {
            "summary": "Arithmetic off by one error.",
            "failed_stage": "TESTING",
            "probable_root_cause": "add returns a + b + 1 instead of a + b",
            "supporting_evidence": ["Test failure output"],
            "recommended_steps": ["Fix return statement"],
            "suggested_fix": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "confidence": "HIGH",
        }
        mock_get_repo.return_value = {"default_branch": "main"}
        mock_sha.return_value = "d" * 40

        # 1. Diagnose workflow
        res_diag = client.post(
            f"/workflows/{wf_id}/diagnose",
            headers={"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"},
        )
        assert res_diag.status_code == 200
        diag = res_diag.json()["diagnosis"]
        assert diag["confidence"] == "HIGH"
        assert diag["suggested_fix"] is not None

        # 2. Fix proposal from diagnosis
        res_fix = client.post(
            f"/workflows/{wf_id}/fix-proposal",
            headers={"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"},
        )
        assert res_fix.status_code == 200
        fix_data = res_fix.json()
        assert fix_data["status"] == "success"
        # Diagnostic fix suggestions are safely marked NOT_ELIGIBLE until reviewed and tested
        assert fix_data["proposal"]["status"] == NOT_ELIGIBLE

        # 3. Retry guard: non-retryable error (e.g. 401 error) is blocked
        wf_auth = test_wf_store.create_workflow(
            repository="om-3531/devsquad-test",
            issue_number=12,
        )
        wf_auth_fail = wf_auth.workflow_id
        test_wf_store.update_workflow(
            wf_auth_fail,
            status=STATUS_FAILED,
            stage=STAGE_PLANNING,
            error_summary="HTTP 401: Invalid token",
        )
        res_retry_blocked = client.post(
            f"/workflows/{wf_auth_fail}/retry",
            headers={"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"},
        )
        assert res_retry_blocked.status_code == 400
        assert "authentication error" in res_retry_blocked.json()["detail"].lower()


def test_workflow_network_read_error_returns_502_with_clear_message(monkeypatch):
    """Verify that a network ReadError during workflow execution returns 502 with helpful details."""
    import httpx
    from backend.main import issue_service

    async def mock_get_issue(*args, **kwargs):
        raise httpx.ReadError("Connection reset by peer while reading HTTP payload")

    monkeypatch.setattr(issue_service, "get_issue", mock_get_issue)

    response = client.post(
        "/agents/workflow",
        json={"repo": "om-3531/DevSquad", "issue_number": 1, "target_path": None},
    )

    assert response.status_code == 502
    data = response.json()
    assert "ReadError" in data["detail"]
    assert "Network communication error" in data["detail"]
    assert "Check GitHub connectivity" in data["detail"]
    # Ensure it is NOT the truncated "Workflow failed: ReadError: "
    assert not data["detail"].endswith("ReadError:")



def teardown_module():
    """Cleanup test databases."""
    gc.collect()
    try:
        if prop_db.exists():
            prop_db.unlink()
        if wf_db.exists():
            wf_db.unlink()
        if test_db_dir.exists():
            test_db_dir.rmdir()
    except Exception:
        pass
