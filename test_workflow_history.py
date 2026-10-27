"""Tests for workflow execution history, events tracking, pagination, stats, and auth."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.auth import (
    ApprovalRateLimiter,
    AuthenticatedIdentity,
    BearerTokenAuthenticator,
    PROPOSAL_APPROVER,
    PROPOSAL_READER,
    WORKFLOW_READER,
)
from backend.main import app, approval_rate_limiter, workflow_store, proposal_store
from backend.workflows import (
    WorkflowStore,
    WorkflowStorageError,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_CANCELLED,
    AGENT_PENDING,
    AGENT_RUNNING,
    AGENT_COMPLETED,
    AGENT_FAILED,
    STAGE_INITIALIZING,
    STAGE_PLANNING,
    STAGE_CODING,
    STAGE_TESTING,
    STAGE_REVIEWING,
    STAGE_COMPLETED,
    STAGE_FAILED,
    EVENT_WORKFLOW_STARTED,
    EVENT_PLANNER_STARTED,
    EVENT_PLANNER_COMPLETED,
    EVENT_CODE_GENERATION_STARTED,
    EVENT_CODE_GENERATION_COMPLETED,
    EVENT_TESTS_STARTED,
    EVENT_TESTS_COMPLETED,
    EVENT_REVIEWER_STARTED,
    EVENT_REVIEWER_COMPLETED,
    EVENT_PROPOSAL_CREATED,
    EVENT_WORKFLOW_SUCCEEDED,
    EVENT_WORKFLOW_FAILED,
)

READER_TOKEN = "devsquad-test-reader-token-1111111111111111"
APPROVER_TOKEN = "devsquad-test-approver-token-2222222222222222"
WORKFLOW_READER_TOKEN = "devsquad-test-wf-reader-token-55555555555"
EXPIRED_TOKEN = "devsquad-test-expired-token-4444444444444444"

TEST_TOKENS_CONFIG = {
    READER_TOKEN: {
        "subject": "reader@devsquad.local",
        "roles": [PROPOSAL_READER],
    },
    APPROVER_TOKEN: {
        "subject": "approver@devsquad.local",
        "roles": [PROPOSAL_APPROVER],
    },
    WORKFLOW_READER_TOKEN: {
        "subject": "wfreader@devsquad.local",
        "roles": [WORKFLOW_READER],
    },
    EXPIRED_TOKEN: {
        "subject": "expired@devsquad.local",
        "roles": [WORKFLOW_READER],
        "expires_at": "2020-01-01T00:00:00Z",
    },
}


@pytest.fixture
def isolated_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_workflows.sqlite3"


@pytest.fixture
def wf_store(isolated_db_path: Path) -> WorkflowStore:
    return WorkflowStore(isolated_db_path)


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch, isolated_db_path: Path):
    monkeypatch.setenv("APPROVAL_AUTH_TOKENS", json.dumps(TEST_TOKENS_CONFIG))
    isolated_wf_store = WorkflowStore(isolated_db_path)
    monkeypatch.setattr("backend.main.workflow_store", isolated_wf_store)
    client = TestClient(app)
    return client, isolated_wf_store


def test_workflow_store_crud_and_events(wf_store: WorkflowStore):
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    wf = wf_store.create_workflow(
        repository="om-3531/DevSquad",
        issue_number=42,
        workflow_id="wf-100",
        status=STATUS_RUNNING,
        stage=STAGE_INITIALIZING,
        metadata={"custom": "meta"},
        now=now,
    )
    assert wf.workflow_id == "wf-100"
    assert wf.repository == "om-3531/DevSquad"
    assert wf.issue_number == 42
    assert wf.status == STATUS_RUNNING
    assert wf.stage == STAGE_INITIALIZING
    assert wf.planner_status == AGENT_PENDING
    assert wf.started_at == now
    assert wf.completed_at is None
    assert wf.duration_seconds is None

    # Record event
    event = wf_store.record_event(
        "wf-100",
        event_type=EVENT_WORKFLOW_STARTED,
        stage=STAGE_INITIALIZING,
        message="Workflow started successfully",
        timestamp=now,
    )
    assert event.event_id == 1
    assert event.workflow_id == "wf-100"
    assert event.event_type == EVENT_WORKFLOW_STARTED

    # Update workflow through stages
    later = now + timedelta(seconds=45)
    updated = wf_store.update_workflow(
        "wf-100",
        status=STATUS_SUCCESS,
        stage=STAGE_COMPLETED,
        planner_status=AGENT_COMPLETED,
        coder_status=AGENT_COMPLETED,
        tester_status=AGENT_COMPLETED,
        reviewer_status=AGENT_COMPLETED,
        proposal_id="prop-xyz",
        now=later,
    )
    assert updated is not None
    assert updated.status == STATUS_SUCCESS
    assert updated.stage == STAGE_COMPLETED
    assert updated.planner_status == AGENT_COMPLETED
    assert updated.coder_status == AGENT_COMPLETED
    assert updated.tester_status == AGENT_COMPLETED
    assert updated.reviewer_status == AGENT_COMPLETED
    assert updated.proposal_id == "prop-xyz"
    assert updated.completed_at == later
    assert updated.duration_seconds == 45.0

    # Retrieve workflow
    retrieved = wf_store.get_workflow("wf-100")
    assert retrieved is not None
    assert retrieved.to_dict()["workflow_id"] == "wf-100"
    assert retrieved.to_dict()["metadata"] == {"custom": "meta"}

    # Retrieve events
    events = wf_store.get_workflow_events("wf-100")
    assert len(events) == 1
    assert events[0].message == "Workflow started successfully"


def test_workflow_secret_sanitization(wf_store: WorkflowStore):
    wf = wf_store.create_workflow(repository="test/repo", issue_number=1)
    ev = wf_store.record_event(
        wf.workflow_id,
        event_type=EVENT_WORKFLOW_FAILED,
        stage=STAGE_FAILED,
        message="Failed with ghp_123456789012345678901234567890 and Bearer secret-token-abcdefgh",
    )
    assert "ghp_123456789012345678901234567890" not in ev.message
    assert "[REDACTED_GITHUB_TOKEN]" in ev.message
    assert "secret-token-abcdefgh" not in ev.message
    assert "Bearer [REDACTED_TOKEN]" in ev.message

    updated = wf_store.update_workflow(
        wf.workflow_id,
        status=STATUS_FAILED,
        error_summary="Exception: password='super_secret_password'",
    )
    assert "super_secret_password" not in updated.error_summary
    assert "[REDACTED]" in updated.error_summary


def test_workflow_pagination_and_filtering(wf_store: WorkflowStore):
    base_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    for i in range(15):
        t = base_time + timedelta(minutes=i)
        status = STATUS_SUCCESS if i % 2 == 0 else STATUS_FAILED
        repo = "org/repo-a" if i < 10 else "org/repo-b"
        wf_store.create_workflow(
            repository=repo,
            issue_number=i + 1,
            workflow_id=f"wf-{i:02d}",
            status=status,
            now=t,
        )

    # All workflows default pagination
    items, total = wf_store.list_workflows(limit=5, offset=0)
    assert total == 15
    assert len(items) == 5
    # Order should be started_at desc
    assert items[0].workflow_id == "wf-14"
    assert items[4].workflow_id == "wf-10"

    # Second page
    page2, _ = wf_store.list_workflows(limit=5, offset=5)
    assert len(page2) == 5
    assert page2[0].workflow_id == "wf-09"

    # Filter by status
    success_items, success_total = wf_store.list_workflows(status=STATUS_SUCCESS)
    assert success_total == 8
    assert all(w.status == STATUS_SUCCESS for w in success_items)

    # Filter by repo (exact)
    repo_b_items, repo_b_total = wf_store.list_workflows(repository="org/repo-b")
    assert repo_b_total == 5
    assert all(w.repository == "org/repo-b" for w in repo_b_items)

    # Substring repo filter regression (e.g. searching 'repo-b' or 'repo-a')
    sub_items, sub_total = wf_store.list_workflows(repository="repo-b")
    assert sub_total == 5
    assert all(w.repository == "org/repo-b" for w in sub_items)

    # Combined status and substring repo filter regression
    failed_sub_items, failed_sub_total = wf_store.list_workflows(status=STATUS_FAILED, repository="repo-b")
    assert failed_sub_total > 0
    assert all(w.status == STATUS_FAILED and "repo-b" in w.repository for w in failed_sub_items)


def test_workflow_stats_calculation(wf_store: WorkflowStore):
    now = datetime.now(UTC)
    # Empty stats
    empty_stats = wf_store.get_stats()
    assert empty_stats["total_executions"] == 0
    assert empty_stats["success_rate"] == 0.0

    # 3 success, 1 failed, 1 running
    w1 = wf_store.create_workflow(repository="a/b", status=STATUS_SUCCESS, now=now)
    wf_store.update_workflow(w1.workflow_id, status=STATUS_SUCCESS, completed_at=now + timedelta(seconds=10))

    w2 = wf_store.create_workflow(repository="a/b", status=STATUS_SUCCESS, now=now)
    wf_store.update_workflow(w2.workflow_id, status=STATUS_SUCCESS, completed_at=now + timedelta(seconds=20))

    w3 = wf_store.create_workflow(repository="a/b", status=STATUS_SUCCESS, now=now)
    wf_store.update_workflow(w3.workflow_id, status=STATUS_SUCCESS, completed_at=now + timedelta(seconds=30))

    w4 = wf_store.create_workflow(repository="a/b", status=STATUS_FAILED, now=now)
    wf_store.update_workflow(w4.workflow_id, status=STATUS_FAILED, completed_at=now + timedelta(seconds=15))

    wf_store.create_workflow(repository="a/b", status=STATUS_RUNNING, now=now)

    stats = wf_store.get_stats()
    assert stats["total_executions"] == 5
    assert stats["successful_executions"] == 3
    assert stats["failed_executions"] == 1
    assert stats["running_executions"] == 1
    assert stats["success_rate"] == 60.0
    assert stats["average_duration_seconds"] == 18.75


def test_workflows_api_authentication(auth_client):
    client, wf_store = auth_client

    # Create dummy workflow
    wf_store.create_workflow(repository="a/b", issue_number=1, workflow_id="wf-abc")

    # Missing auth token -> 401
    res = client.get("/workflows")
    assert res.status_code == 401
    assert "credentials" in res.json()["detail"].lower()

    # Invalid token -> 401
    res = client.get("/workflows", headers={"Authorization": "Bearer bad-token"})
    assert res.status_code == 401

    # Expired token -> 401
    res = client.get("/workflows", headers={"Authorization": f"Bearer {EXPIRED_TOKEN}"})
    assert res.status_code == 401

    # Valid reader token -> 200
    res = client.get("/workflows", headers={"Authorization": f"Bearer {READER_TOKEN}"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["total"] == 1

    # Valid approver token -> 200
    res = client.get("/workflows", headers={"Authorization": f"Bearer {APPROVER_TOKEN}"})
    assert res.status_code == 200

    # Valid workflow reader token -> 200
    res = client.get("/workflows", headers={"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"})
    assert res.status_code == 200


def test_workflows_unconfigured_auth_returns_503(monkeypatch: pytest.MonkeyPatch, isolated_db_path: Path):
    monkeypatch.delenv("APPROVAL_AUTH_TOKENS", raising=False)
    monkeypatch.setattr("backend.config.settings.APPROVAL_AUTH_TOKENS", None)
    isolated_wf_store = WorkflowStore(isolated_db_path)
    monkeypatch.setattr("backend.main.workflow_store", isolated_wf_store)
    client = TestClient(app)

    res = client.get("/workflows")
    assert res.status_code == 503
    assert "not configured" in res.json()["detail"].lower()

    stats_res = client.get("/workflows/stats")
    assert stats_res.status_code == 503
    assert "not configured" in stats_res.json()["detail"].lower()


def test_workflow_details_and_events_api(auth_client):
    client, wf_store = auth_client
    wf = wf_store.create_workflow(
        repository="org/repo",
        issue_number=10,
        workflow_id="wf-test-id",
        status=STATUS_RUNNING,
    )
    wf_store.record_event(
        "wf-test-id",
        event_type=EVENT_WORKFLOW_STARTED,
        stage=STAGE_INITIALIZING,
        message="Workflow initialized",
    )
    wf_store.record_event(
        "wf-test-id",
        event_type=EVENT_PLANNER_COMPLETED,
        stage=STAGE_PLANNING,
        message="Plan created",
    )

    headers = {"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"}

    # Fetch workflow details
    res = client.get("/workflows/wf-test-id", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["workflow"]["workflow_id"] == "wf-test-id"
    assert body["workflow"]["repository"] == "org/repo"

    # Fetch workflow events
    events_res = client.get("/workflows/wf-test-id/events", headers=headers)
    assert events_res.status_code == 200
    ev_data = events_res.json()
    assert ev_data["status"] == "success"
    assert len(ev_data["events"]) == 2
    assert ev_data["events"][0]["event_type"] == EVENT_WORKFLOW_STARTED
    assert ev_data["events"][1]["event_type"] == EVENT_PLANNER_COMPLETED

    # Missing workflow ID -> 404
    missing_res = client.get("/workflows/non-existent-id", headers=headers)
    assert missing_res.status_code == 404

    missing_events_res = client.get("/workflows/non-existent-id/events", headers=headers)
    assert missing_events_res.status_code == 404


def test_workflows_stats_api(auth_client):
    client, wf_store = auth_client
    wf_store.create_workflow(repository="a/b", status=STATUS_SUCCESS)
    wf_store.create_workflow(repository="a/b", status=STATUS_FAILED)

    headers = {"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"}
    res = client.get("/workflows/stats", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["stats"]["total_executions"] == 2
    assert body["stats"]["successful_executions"] == 1
    assert body["stats"]["failed_executions"] == 1


def test_workflows_query_params_validation(auth_client):
    client, _ = auth_client
    headers = {"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"}

    # Invalid status filter -> 400
    res = client.get("/workflows?status=UNKNOWN_STATUS", headers=headers)
    assert res.status_code == 400
    assert "invalid status" in res.json()["detail"].lower()

    # Invalid pagination: negative limit -> 422
    res = client.get("/workflows?limit=0", headers=headers)
    assert res.status_code == 422

    # Negative offset -> 422
    res = client.get("/workflows?offset=-5", headers=headers)
    assert res.status_code == 422


def test_workflow_database_error_handling(monkeypatch: pytest.MonkeyPatch, auth_client):
    client, wf_store = auth_client
    headers = {"Authorization": f"Bearer {WORKFLOW_READER_TOKEN}"}

    def failing_list(*args, **kwargs):
        raise WorkflowStorageError("Simulated DB failure")

    monkeypatch.setattr(wf_store, "list_workflows", failing_list)
    res = client.get("/workflows", headers=headers)
    assert res.status_code == 503
    assert "unavailable" in res.json()["detail"].lower()


def test_end_to_end_workflow_run_creates_history_record_and_events(monkeypatch: pytest.MonkeyPatch, isolated_db_path: Path):
    test_store = WorkflowStore(isolated_db_path)
    monkeypatch.setattr("backend.main.workflow_store", test_store)

    issue = {"title": "Feature task", "body": "Implement task", "number": 7}
    client = TestClient(app)

    with (
        patch("backend.main.issue_service.get_issue", new_callable=AsyncMock, return_value=issue),
        patch("backend.main.repository_service.get_repository", new_callable=AsyncMock, return_value={"default_branch": "main"}),
        patch("backend.main.repository_service.get_tree", new_callable=AsyncMock, return_value=["main.py"]),
        patch("backend.main.repository_service.get_branch_sha", new_callable=AsyncMock, return_value="a" * 40),
        patch("backend.main.repository_service.get_file", new_callable=AsyncMock, return_value={"decoded_content": "# code", "sha": "b" * 40}),
        patch("backend.main.planner_agent.create_plan", return_value={"steps": ["Plan"]}),
        patch("backend.main.coder_agent.generate_code", return_value="def solution(): pass"),
        patch("backend.main.tester_agent.generate_tests", return_value="def test_solution(): pass"),
        patch("backend.main.reviewer_agent.review_code", return_value="VERDICT: APPROVE"),
        patch("backend.main.generated_test_runner.run_generated_tests", return_value={"passed": True, "status": "TESTS_PASSED", "summary": "1 passed"}),
    ):
        response = client.post("/agents/workflow", json={"repo": "om-3531/DevSquad", "issue_number": 7})

    assert response.status_code == 200
    data = response.json()
    wf_id = data.get("workflow_id")
    assert wf_id is not None

    record = test_store.get_workflow(wf_id)
    assert record is not None
    assert record.status == STATUS_SUCCESS
    assert record.stage == STAGE_COMPLETED
    assert record.planner_status == AGENT_COMPLETED
    assert record.coder_status == AGENT_COMPLETED
    assert record.tester_status == AGENT_COMPLETED
    assert record.reviewer_status == AGENT_COMPLETED
    assert record.proposal_id == data["proposal_id"]
    assert record.duration_seconds is not None and record.duration_seconds >= 0.0

    events = test_store.get_workflow_events(wf_id)
    types = [e.event_type for e in events]
    assert EVENT_WORKFLOW_STARTED in types
    assert EVENT_PLANNER_STARTED in types
    assert EVENT_PLANNER_COMPLETED in types
    assert EVENT_CODE_GENERATION_STARTED in types
    assert EVENT_CODE_GENERATION_COMPLETED in types
    assert EVENT_REVIEWER_STARTED in types
    assert EVENT_REVIEWER_COMPLETED in types
    assert EVENT_TESTS_STARTED in types
    assert EVENT_TESTS_COMPLETED in types
    assert EVENT_PROPOSAL_CREATED in types
    assert EVENT_WORKFLOW_SUCCEEDED in types


def test_end_to_end_workflow_failure_records_failed_status_and_event(monkeypatch: pytest.MonkeyPatch, isolated_db_path: Path):
    test_store = WorkflowStore(isolated_db_path)
    monkeypatch.setattr("backend.main.workflow_store", test_store)

    issue = {"title": "Failing task", "body": "Fails", "number": 9}
    client = TestClient(app)

    with (
        patch("backend.main.issue_service.get_issue", new_callable=AsyncMock, return_value=issue),
        patch("backend.main.repository_service.get_repository", new_callable=AsyncMock, return_value={"default_branch": "main"}),
        patch("backend.main.repository_service.get_tree", new_callable=AsyncMock, return_value=["main.py"]),
        patch("backend.main.repository_service.get_branch_sha", new_callable=AsyncMock, return_value="a" * 40),
        patch("backend.main.planner_agent.create_plan", return_value={"steps": ["Plan"]}),
        patch("backend.main.coder_agent.generate_code", side_effect=RuntimeError("AI generation failed unexpectedly")),
    ):
        response = client.post("/agents/workflow", json={"repo": "om-3531/DevSquad", "issue_number": 9})

    assert response.status_code == 500
    workflows, total = test_store.list_workflows()
    assert total == 1
    record = workflows[0]
    assert record.status == STATUS_FAILED
    assert record.stage == STAGE_CODING
    assert record.coder_status == AGENT_FAILED
    assert "AI generation failed unexpectedly" in record.error_summary

    events = test_store.get_workflow_events(record.workflow_id)
    assert any(e.event_type == EVENT_WORKFLOW_FAILED for e in events)
