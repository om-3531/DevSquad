"""Comprehensive verification suite for DevSquad proposal authentication, authorization, auditing, and rate limiting."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.auth import (
    ApprovalRateLimiter,
    AuthenticatedIdentity,
    AuthenticationConfigurationError,
    AuthenticationError,
    AuthorizationError,
    BearerTokenAuthenticator,
    PROPOSAL_APPROVER,
    PROPOSAL_READER,
)
from backend.main import app, approval_rate_limiter, proposal_store
from backend.proposals import APPROVED, EXPIRED, NOT_ELIGIBLE, PENDING_REVIEW, STALE, StaleProposalError, ProposalStore


READER_TOKEN = "devsquad-test-reader-token-1111111111111111"
APPROVER_TOKEN = "devsquad-test-approver-token-2222222222222222"
ADMIN_TOKEN = "devsquad-test-admin-token-3333333333333333"
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
    ADMIN_TOKEN: {
        "subject": "admin@devsquad.local",
        "roles": [PROPOSAL_READER, PROPOSAL_APPROVER],
    },
    EXPIRED_TOKEN: {
        "subject": "expired@devsquad.local",
        "roles": [PROPOSAL_APPROVER],
        "expires_at": "2020-01-01T00:00:00Z",
    },
}


@pytest.fixture
def test_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_proposals.sqlite3"


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch, test_db_path: Path):
    monkeypatch.setenv("APPROVAL_AUTH_TOKENS", json.dumps(TEST_TOKENS_CONFIG))
    # Point proposal_store to isolated test database
    isolated_store = ProposalStore(test_db_path)
    monkeypatch.setattr("backend.main.proposal_store", isolated_store)
    approval_rate_limiter.reset()
    return isolated_store


@pytest.fixture
def eligible_proposal(auth_env: ProposalStore):
    return auth_env.create(
        repository="om-3531/DevSquad",
        issue_number=1,
        target_branch="main",
        target_path="backend/solution.py",
        operation="modify",
        proposed_content="def solution():\n    return True\n",
        diff_preview={"reliable": True, "changed": True, "diff": "+def solution():\n+    return True\n"},
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        base_commit_sha="a" * 40,
        target_file_blob_sha="b" * 40,
    )


# ---------------------------------------------------------------------------
# 1. Configuration & Fail-Closed Behavior
# ---------------------------------------------------------------------------

def test_missing_auth_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch, test_db_path: Path):
    monkeypatch.delenv("APPROVAL_AUTH_TOKENS", raising=False)
    monkeypatch.setattr("backend.config.settings.APPROVAL_AUTH_TOKENS", None)
    monkeypatch.setattr("backend.main.proposal_store", ProposalStore(test_db_path))

    client = TestClient(app)
    response_get = client.get("/proposals/any-id")
    assert response_get.status_code == 503
    assert "not configured" in response_get.json()["detail"].lower()

    response_post = client.post("/proposals/any-id/approve", json={"content_digest": "a" * 64})
    assert response_post.status_code == 503
    assert "not configured" in response_post.json()["detail"].lower()


def test_malformed_auth_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch, test_db_path: Path):
    monkeypatch.setenv("APPROVAL_AUTH_TOKENS", "invalid-json{{{")
    monkeypatch.setattr("backend.main.proposal_store", ProposalStore(test_db_path))

    client = TestClient(app)
    response = client.get("/proposals/any-id")
    assert response.status_code == 503


def test_empty_tokens_dictionary_fails_closed():
    with pytest.raises(AuthenticationConfigurationError):
        BearerTokenAuthenticator.from_json("{}")


# ---------------------------------------------------------------------------
# 2. Authentication: Credentials Required, Invalid, and Expired
# ---------------------------------------------------------------------------

def test_missing_authorization_header_returns_401(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)

    get_resp = client.get(f"/proposals/{eligible_proposal.proposal_id}")
    assert get_resp.status_code == 401
    assert get_resp.headers.get("WWW-Authenticate") == "Bearer"

    approve_resp = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/approve",
        json={"content_digest": eligible_proposal.content_digest},
    )
    assert approve_resp.status_code == 401
    assert approve_resp.headers.get("WWW-Authenticate") == "Bearer"


def test_invalid_bearer_token_returns_401(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": "Bearer wrong-or-forged-token"}

    resp = client.get(f"/proposals/{eligible_proposal.proposal_id}", headers=headers)
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


def test_non_bearer_scheme_returns_401(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Basic {APPROVER_TOKEN}"}

    resp = client.get(f"/proposals/{eligible_proposal.proposal_id}", headers=headers)
    assert resp.status_code == 401


def test_expired_token_returns_401(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {EXPIRED_TOKEN}"}

    resp = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/approve",
        headers=headers,
        json={"content_digest": eligible_proposal.content_digest},
    )
    assert resp.status_code == 401


def test_bearer_token_formatting_variations_succeed(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    valid_variations = [
        f"Bearer {READER_TOKEN}",
        f"Bearer  {READER_TOKEN} ",
        f"Bearer \"{READER_TOKEN}\"",
        f"Bearer '{READER_TOKEN}'",
        f"Bearer Bearer {READER_TOKEN}",
        f"bearer {READER_TOKEN}",
        f"Bearer\t{READER_TOKEN}",
        f"{READER_TOKEN}",
    ]
    for auth_header in valid_variations:
        resp = client.get(
            f"/proposals/{eligible_proposal.proposal_id}",
            headers={"Authorization": auth_header},
        )
        assert resp.status_code == 200, f"Failed for header: {repr(auth_header)}"
        assert resp.json()["status"] == "success"



def test_client_supplied_identity_is_ignored(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    # Trying to spoof subject or roles in query or headers without a valid approver token
    headers = {"Authorization": f"Bearer {READER_TOKEN}"}
    resp = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/approve?subject=admin@devsquad.local&roles=proposal_approver",
        headers=headers,
        json={"content_digest": eligible_proposal.content_digest},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. Role-Based Authorization
# ---------------------------------------------------------------------------

def test_reader_role_can_retrieve_proposal(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {READER_TOKEN}"}

    resp = client.get(f"/proposals/{eligible_proposal.proposal_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["proposal"]["proposal_id"] == eligible_proposal.proposal_id
    assert "proposed_content" not in data["proposal"]  # Not leaked in public_dict
    assert "approved_by" not in data["proposal"]


def test_approver_only_token_cannot_read_proposals(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {APPROVER_TOKEN}"}

    resp = client.get(f"/proposals/{eligible_proposal.proposal_id}", headers=headers)
    assert resp.status_code == 403
    assert "not authorized" in resp.json()["detail"].lower()


def test_reader_token_cannot_approve_proposals(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {READER_TOKEN}"}

    resp = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/approve",
        headers=headers,
        json={"content_digest": eligible_proposal.content_digest},
    )
    assert resp.status_code == 403
    assert "not authorized" in resp.json()["detail"].lower()


def test_reader_token_cannot_start_github_write(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    response = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/write",
        headers={"Authorization": f"Bearer {READER_TOKEN}"},
    )
    assert response.status_code == 403


def test_disabled_github_write_endpoint_fails_closed(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    response = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/write",
        headers={"Authorization": f"Bearer {APPROVER_TOKEN}"},
    )
    assert response.status_code == 503
    assert "disabled" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 4. Successful Approval & Transactional Audit Consistency
# ---------------------------------------------------------------------------

def test_successful_approval_updates_status_and_commits_audit(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {APPROVER_TOKEN}"}

    resp = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/approve",
        headers=headers,
        json={"content_digest": eligible_proposal.content_digest},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["proposal"]["status"] == APPROVED

    # Check store state directly
    updated = auth_env.get(eligible_proposal.proposal_id)
    assert updated.status == APPROVED
    assert updated.approved_by == "approver@devsquad.local"
    assert updated.approved_at is not None

    # Check audit log entries
    audit = auth_env.audit_entries(eligible_proposal.proposal_id)
    assert len(audit) == 1
    assert audit[0]["actor_subject"] == "approver@devsquad.local"
    assert audit[0]["action"] == "APPROVED"
    assert audit[0]["content_digest"] == eligible_proposal.content_digest
    assert audit[0]["occurred_at"] is not None


def test_admin_with_both_roles_can_read_and_approve(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    get_resp = client.get(f"/proposals/{eligible_proposal.proposal_id}", headers=headers)
    assert get_resp.status_code == 200

    approve_resp = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/approve",
        headers=headers,
        json={"content_digest": eligible_proposal.content_digest},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["proposal"]["status"] == APPROVED


# ---------------------------------------------------------------------------
# 5. Rejection Handling: Duplicate, Expired, Mismatch, Ineligible
# ---------------------------------------------------------------------------

def test_duplicate_approval_rejected_and_audited(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {APPROVER_TOKEN}"}
    payload = {"content_digest": eligible_proposal.content_digest}

    # First approval succeeds
    first = client.post(f"/proposals/{eligible_proposal.proposal_id}/approve", headers=headers, json=payload)
    assert first.status_code == 200

    # Second approval fails with 409 Conflict
    second = client.post(f"/proposals/{eligible_proposal.proposal_id}/approve", headers=headers, json=payload)
    assert second.status_code == 409
    assert "already been approved" in second.json()["detail"].lower()

    # Verify audit contains both APPROVED and REJECTED_ALREADY_APPROVED
    audit = auth_env.audit_entries(eligible_proposal.proposal_id)
    actions = [entry["action"] for entry in audit]
    assert "APPROVED" in actions
    assert "REJECTED_ALREADY_APPROVED" in actions


def test_expired_proposal_cannot_be_approved(auth_env: ProposalStore):
    past = datetime.now(UTC) - timedelta(hours=2)
    expired = auth_env.create(
        repository="om-3531/DevSquad",
        issue_number=2,
        target_branch="main",
        target_path="backend/old.py",
        operation="modify",
        proposed_content="old",
        diff_preview={"reliable": True, "changed": True, "diff": "+old"},
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        now=past,
    )

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {APPROVER_TOKEN}"}
    resp = client.post(
        f"/proposals/{expired.proposal_id}/approve",
        headers=headers,
        json={"content_digest": expired.content_digest},
    )
    assert resp.status_code == 410
    assert "expired" in resp.json()["detail"].lower()

    audit = auth_env.audit_entries(expired.proposal_id)
    assert any(entry["action"] == "REJECTED_EXPIRED" for entry in audit)


def test_content_digest_mismatch_rejected(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {APPROVER_TOKEN}"}
    fake_digest = "0" * 64

    resp = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/approve",
        headers=headers,
        json={"content_digest": fake_digest},
    )
    assert resp.status_code == 409
    assert "does not match" in resp.json()["detail"].lower()

    audit = auth_env.audit_entries(eligible_proposal.proposal_id)
    assert any(entry["action"] == "REJECTED_DIGEST_MISMATCH" for entry in audit)


def test_ineligible_proposal_cannot_be_approved(auth_env: ProposalStore):
    ineligible = auth_env.create(
        repository="om-3531/DevSquad",
        issue_number=3,
        target_branch="main",
        target_path="backend/draft.py",
        operation="modify",
        proposed_content="draft",
        diff_preview={"reliable": False, "changed": False},
        quality_gate_status="GENERATED_TESTS_FAILED",
    )
    assert ineligible.status == NOT_ELIGIBLE

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {APPROVER_TOKEN}"}
    resp = client.post(
        f"/proposals/{ineligible.proposal_id}/approve",
        headers=headers,
        json={"content_digest": ineligible.content_digest},
    )
    assert resp.status_code == 409

    audit = auth_env.audit_entries(ineligible.proposal_id)
    assert any(entry["action"] == "REJECTED_NOT_ELIGIBLE" for entry in audit)


# ---------------------------------------------------------------------------
# 6. Rate Limiting on Approval Attempts
# ---------------------------------------------------------------------------

def test_approval_rate_limiter_blocks_excessive_attempts(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {APPROVER_TOKEN}"}
    payload = {"content_digest": eligible_proposal.content_digest}

    # Reset limiter for deterministic test
    approval_rate_limiter.reset()

    # Fire 5 attempts with mismatched digest to exhaust attempts
    wrong_payload = {"content_digest": "f" * 64}
    for _ in range(5):
        resp = client.post(
            f"/proposals/{eligible_proposal.proposal_id}/approve",
            headers=headers,
            json=wrong_payload,
        )
        assert resp.status_code == 409

    # 6th attempt should be blocked by rate limit (HTTP 429)
    blocked = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/approve",
        headers=headers,
        json=payload,
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert int(blocked.headers["Retry-After"]) >= 1


# ---------------------------------------------------------------------------
# 7. Audit Log Privacy & Immutability
# ---------------------------------------------------------------------------

def test_audit_entries_never_leak_candidate_code_or_tokens(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {APPROVER_TOKEN}"}
    client.post(
        f"/proposals/{eligible_proposal.proposal_id}/approve",
        headers=headers,
        json={"content_digest": eligible_proposal.content_digest},
    )

    audit = auth_env.audit_entries(eligible_proposal.proposal_id)
    for entry in audit:
        # Verify candidate code is never recorded
        assert "def solution" not in str(entry)
        # Verify bearer token is never recorded
        assert APPROVER_TOKEN not in str(entry)
        assert READER_TOKEN not in str(entry)


def test_public_dict_does_not_contain_candidate_code(eligible_proposal):
    public_view = eligible_proposal.public_dict()
    assert "proposed_content" not in public_view
    assert "approved_by" not in public_view


def test_revision_check_rejects_changed_branch(auth_env: ProposalStore, eligible_proposal):
    with pytest.raises(StaleProposalError):
        auth_env.verify_revision(eligible_proposal.proposal_id, "c" * 40, "b" * 40)
    assert auth_env.get(eligible_proposal.proposal_id).status == STALE


def test_revision_check_rejects_changed_file(auth_env: ProposalStore, eligible_proposal):
    with pytest.raises(StaleProposalError):
        auth_env.verify_revision(eligible_proposal.proposal_id, "a" * 40, "c" * 40)
    assert auth_env.get(eligible_proposal.proposal_id).status == STALE


def test_revision_check_rejects_missing_metadata(auth_env: ProposalStore):
    proposal = auth_env.create(repository="om-3531/DevSquad", issue_number=4, target_branch="main", target_path="backend/new.py", operation="modify", proposed_content="new", diff_preview={"reliable": True, "changed": True}, quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED")
    with pytest.raises(StaleProposalError):
        auth_env.verify_revision(proposal.proposal_id, "a" * 40, "b" * 40)
    assert auth_env.get(proposal.proposal_id).status == STALE


def test_revision_check_accepts_unchanged_remote_state(auth_env: ProposalStore, eligible_proposal):
    verified = auth_env.verify_revision(eligible_proposal.proposal_id, "a" * 40, "b" * 40)
    assert verified.status == PENDING_REVIEW


# ---------------------------------------------------------------------------
# 8. Proposal Rejection & Audit Endpoint Verification
# ---------------------------------------------------------------------------

def test_approver_can_reject_proposal(auth_env: ProposalStore, eligible_proposal, monkeypatch: pytest.MonkeyPatch):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {APPROVER_TOKEN}"}
    response = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/reject",
        headers=headers,
        json={"reason": "Candidate logic needs manual security review."},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["proposal"]["status"] == "REJECTED"

    # Verify audit entry is recorded
    audit = auth_env.audit_entries(eligible_proposal.proposal_id)
    assert len(audit) == 1
    assert "REJECTED" in audit[0]["action"]
    assert audit[0]["actor_subject"] == "approver@devsquad.local"


def test_reject_proposal_requires_approver_role(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    # Reader token should be forbidden (403)
    response_reader = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/reject",
        headers={"Authorization": f"Bearer {READER_TOKEN}"},
        json={"reason": "Reader trying to reject"},
    )
    assert response_reader.status_code == 403

    # No token should be unauthorized (401)
    response_anon = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/reject",
        json={"reason": "Anonymous"},
    )
    assert response_anon.status_code == 401


def test_resolved_proposal_cannot_be_re_resolved(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {APPROVER_TOKEN}"}

    # Reject once
    res1 = client.post(f"/proposals/{eligible_proposal.proposal_id}/reject", headers=headers)
    assert res1.status_code == 200

    # Rejecting again should fail with 409
    res2 = client.post(f"/proposals/{eligible_proposal.proposal_id}/reject", headers=headers)
    assert res2.status_code == 409

    # Approving a rejected proposal should fail with 409
    res3 = client.post(
        f"/proposals/{eligible_proposal.proposal_id}/approve",
        headers=headers,
        json={"content_digest": eligible_proposal.content_digest},
    )
    assert res3.status_code == 409


def test_ineligible_proposal_cannot_be_approved_or_rejected(auth_env: ProposalStore):
    ineligible = auth_env.create(
        repository="om-3531/DevSquad",
        issue_number=99,
        target_branch="main",
        target_path="backend/ineligible.py",
        operation="modify",
        proposed_content="broken code",
        diff_preview={"reliable": True, "changed": True},
        quality_gate_status="REQUEST_CHANGES",
    )
    assert ineligible.status == "NOT_ELIGIBLE"

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {APPROVER_TOKEN}"}

    # Approval attempt fails with 409
    res_app = client.post(
        f"/proposals/{ineligible.proposal_id}/approve",
        headers=headers,
        json={"content_digest": ineligible.content_digest},
    )
    assert res_app.status_code == 409
    assert "not eligible" in res_app.json()["detail"].lower()

    # Rejection attempt fails with 409
    res_rej = client.post(
        f"/proposals/{ineligible.proposal_id}/reject",
        headers=headers,
        json={"reason": "test"},
    )
    assert res_rej.status_code == 409


def test_proposal_audit_endpoint(auth_env: ProposalStore, eligible_proposal):
    client = TestClient(app)

    # Approve to generate an audit record
    client.post(
        f"/proposals/{eligible_proposal.proposal_id}/approve",
        headers={"Authorization": f"Bearer {APPROVER_TOKEN}"},
        json={"content_digest": eligible_proposal.content_digest},
    )

    # Reader role can inspect audit records
    res = client.get(
        f"/proposals/{eligible_proposal.proposal_id}/audit",
        headers={"Authorization": f"Bearer {READER_TOKEN}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert len(data["audit_events"]) == 1
    assert data["audit_events"][0]["action"] == "APPROVED"
    assert data["audit_events"][0]["actor_subject"] == "approver@devsquad.local"
    assert "occurred_at" in data["audit_events"][0]
