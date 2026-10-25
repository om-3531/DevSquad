import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.agents.model_manager import sanitize_secret
from backend.auth import PROPOSAL_APPROVER, PROPOSAL_READER
from backend.config import settings
from backend.github.client import GitHubWritesDisabledError
from backend.github.write_orchestrator import ProposalWriteOrchestrator
from backend.proposals import (
    APPROVED,
    COMMITTED,
    EXPIRED,
    PENDING_REVIEW,
    PR_CREATED,
    STALE,
    WRITE_FAILED,
    ProposalStore,
    StaleProposalError,
)


def approved(store):
    proposal = store.create(
        repository="owner/repo",
        issue_number=1,
        target_branch="main",
        target_path="src/a.py",
        operation="modify",
        proposed_content="print('ok')\n",
        diff_preview={"reliable": True, "changed": True},
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        base_commit_sha="a" * 40,
        target_file_blob_sha="b" * 40,
    )
    store.approve(proposal.proposal_id, proposal.content_digest, "approver")
    return proposal


@pytest.fixture
def setup(monkeypatch, tmp_path):
    store = ProposalStore(tmp_path / "proposals.sqlite")
    repo = type("Repo", (), {})()
    github = type("GitHub", (), {})()
    repo.proposal_revision = AsyncMock(return_value=("a" * 40, "b" * 40))
    github.create_branch = AsyncMock(return_value={"ref": "refs/heads/devsquad/proposal-x"})
    github.commit_files = AsyncMock(
        return_value={
            "commit_sha": "c" * 40,
            "commit_url": "https://github.test/commit",
            "branch": "devsquad/proposal-x",
        }
    )
    github.create_pull_request = AsyncMock(
        return_value={"number": 7, "html_url": "https://github.test/pull/7"}
    )
    return store, repo, github


@pytest.mark.asyncio
async def test_disabled_gate_makes_no_github_calls(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", False)
    with pytest.raises(GitHubWritesDisabledError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    repo.proposal_revision.assert_not_called()
    github.create_branch.assert_not_called()


@pytest.mark.asyncio
async def test_unapproved_proposal_is_rejected(setup, monkeypatch):
    store, repo, github = setup
    proposal = store.create(
        repository="owner/repo",
        issue_number=1,
        target_branch="main",
        target_path="src/a.py",
        operation="modify",
        proposed_content="x",
        diff_preview={"reliable": True, "changed": True},
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        base_commit_sha="a" * 40,
        target_file_blob_sha="b" * 40,
    )
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(ValueError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    github.create_branch.assert_not_called()


@pytest.mark.asyncio
async def test_stale_remote_revision_prevents_branch_creation(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    repo.proposal_revision.return_value = ("d" * 40, "b" * 40)
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(RuntimeError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    github.create_branch.assert_not_called()


@pytest.mark.asyncio
async def test_success_records_commit_and_draft_pr(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    result = await ProposalWriteOrchestrator(store, repo, github).execute(
        proposal.proposal_id, "approver"
    )
    persisted = store.get(proposal.proposal_id)
    assert result["pull_request"]["number"] == 7
    assert persisted.status == PR_CREATED
    assert persisted.commit_sha == "c" * 40
    assert persisted.pr_url == "https://github.test/pull/7"


@pytest.mark.asyncio
async def test_commit_failure_prevents_pr_and_is_recorded(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    github.commit_files.side_effect = RuntimeError("mocked commit failure")
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(RuntimeError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    assert store.get(proposal.proposal_id).status == WRITE_FAILED
    github.create_pull_request.assert_not_called()


@pytest.mark.asyncio
async def test_pr_failure_does_not_claim_pr_success(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    github.create_pull_request.side_effect = RuntimeError("mocked pr failure")
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(RuntimeError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    persisted = store.get(proposal.proposal_id)
    assert persisted.status == WRITE_FAILED
    assert persisted.commit_sha == "c" * 40
    assert persisted.pr_number is None


def test_duplicate_write_claim_is_rejected(setup):
    store, _, _ = setup
    proposal = approved(store)
    store.begin_write(proposal.proposal_id, "approver", "devsquad/proposal-1")
    with pytest.raises(ValueError):
        store.begin_write(proposal.proposal_id, "approver", "devsquad/proposal-1")


@pytest.mark.asyncio
async def test_recovery_after_pr_failure_does_not_create_duplicate_commit(
    setup, monkeypatch
):
    store, repo, github = setup
    proposal = approved(store)

    # First attempt: PR fails after commit succeeds
    github.create_pull_request.side_effect = RuntimeError("mocked pr failure")
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(RuntimeError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )

    failed_proposal = store.get(proposal.proposal_id)
    assert failed_proposal.status == WRITE_FAILED
    assert failed_proposal.commit_sha == "c" * 40
    assert failed_proposal.write_branch is not None
    assert github.commit_files.call_count == 1
    assert github.create_branch.call_count == 1

    # Second attempt (safe recovery): PR succeeds
    github.create_branch.reset_mock()
    github.commit_files.reset_mock()
    github.create_pull_request.reset_mock()
    github.create_pull_request.side_effect = None
    github.create_pull_request.return_value = {
        "number": 8,
        "html_url": "https://github.test/pull/8",
    }

    result = await ProposalWriteOrchestrator(store, repo, github).execute(
        proposal.proposal_id, "approver"
    )

    # Assert branch creation and commit were NOT repeated
    github.create_branch.assert_not_called()
    github.commit_files.assert_not_called()

    # Assert PR was created using the existing commit
    github.create_pull_request.assert_called_once()
    assert result["pull_request"]["number"] == 8
    persisted = store.get(proposal.proposal_id)
    assert persisted.status == PR_CREATED
    assert persisted.commit_sha == "c" * 40
    assert persisted.pr_number == 8
    assert persisted.pr_url == "https://github.test/pull/8"

    # Verify audit entries trace
    audit = store.audit_entries(proposal.proposal_id)
    actions = [a["action"] for a in audit]
    assert "APPROVED" in actions
    assert "COMMIT_CREATED" in actions
    assert "WRITE_FAILED" in actions
    assert "DRAFT_PR_CREATED" in actions


@pytest.mark.asyncio
async def test_invalid_target_path_fails_validation(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    with store._connect() as conn:
        conn.execute(
            "UPDATE proposals SET target_path = '../sneaky.py' WHERE proposal_id = ?",
            (proposal.proposal_id,),
        )
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(ValueError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    github.create_branch.assert_not_called()
    github.commit_files.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_repository_fails_validation(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    with store._connect() as conn:
        conn.execute(
            "UPDATE proposals SET repository = 'bad/repo;injection' WHERE proposal_id = ?",
            (proposal.proposal_id,),
        )
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(ValueError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    github.create_branch.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_target_branch_fails_validation(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    with store._connect() as conn:
        conn.execute(
            "UPDATE proposals SET target_branch = 'main..bad' WHERE proposal_id = ?",
            (proposal.proposal_id,),
        )
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(ValueError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    github.create_branch.assert_not_called()


@pytest.mark.asyncio
async def test_expired_proposal_rejected_for_writing(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE proposals SET expires_at = ? WHERE proposal_id = ?",
            (past, proposal.proposal_id),
        )
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(TimeoutError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    github.create_branch.assert_not_called()


@pytest.mark.asyncio
async def test_tampered_content_digest_rejected_for_writing(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    with store._connect() as conn:
        conn.execute(
            "UPDATE proposals SET proposed_content = 'tampered' WHERE proposal_id = ?",
            (proposal.proposal_id,),
        )
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(PermissionError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    github.create_branch.assert_not_called()


@pytest.mark.asyncio
async def test_tampered_review_digest_rejected_for_writing(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    with store._connect() as conn:
        conn.execute(
            "UPDATE proposals SET review_digest = '0000000000000000000000000000000000000000000000000000000000000000' WHERE proposal_id = ?",
            (proposal.proposal_id,),
        )
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(PermissionError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    github.create_branch.assert_not_called()


@pytest.mark.asyncio
async def test_changed_file_blob_sha_marks_proposal_stale(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    # File blob SHA changed remotely
    repo.proposal_revision.return_value = ("a" * 40, "z" * 40)
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(StaleProposalError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    persisted = store.get(proposal.proposal_id)
    assert persisted.status == STALE
    audit = store.audit_entries(proposal.proposal_id)
    assert any(a["action"] == "REJECTED_STALE_REVISION" for a in audit)
    github.create_branch.assert_not_called()


@pytest.mark.asyncio
async def test_unexpected_existing_file_for_create_operation_marks_stale(
    setup, monkeypatch
):
    store, repo, github = setup
    proposal = store.create(
        repository="owner/repo",
        issue_number=2,
        target_branch="main",
        target_path="src/new_file.py",
        operation="create",
        proposed_content="new content\n",
        diff_preview={"reliable": True, "changed": True},
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        base_commit_sha="a" * 40,
        target_file_blob_sha=None,
    )
    store.approve(proposal.proposal_id, proposal.content_digest, "approver")
    # File now exists remotely
    repo.proposal_revision.return_value = ("a" * 40, "f" * 40)
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    with pytest.raises(StaleProposalError):
        await ProposalWriteOrchestrator(store, repo, github).execute(
            proposal.proposal_id, "approver"
        )
    persisted = store.get(proposal.proposal_id)
    assert persisted.status == STALE


@pytest.mark.asyncio
async def test_audit_trail_complete_lifecycle(setup, monkeypatch):
    store, repo, github = setup
    proposal = approved(store)
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    await ProposalWriteOrchestrator(store, repo, github).execute(
        proposal.proposal_id, "approver"
    )
    entries = store.audit_entries(proposal.proposal_id)
    actions = [e["action"] for e in entries]
    assert actions == ["APPROVED", "WRITE_STARTED", "COMMIT_CREATED", "DRAFT_PR_CREATED"]
    for e in entries:
        assert e["actor_subject"] == "approver"
        assert e["occurred_at"] is not None


def test_write_endpoint_auth_matrix(tmp_path, monkeypatch):
    tokens = {
        "reader-tok": {"subject": "reader@test", "roles": [PROPOSAL_READER]},
        "approver-tok": {"subject": "approver@test", "roles": [PROPOSAL_APPROVER]},
    }
    monkeypatch.setenv("APPROVAL_AUTH_TOKENS", json.dumps(tokens))
    import backend.main as main_mod

    test_store = ProposalStore(tmp_path / "proposals.sqlite")
    monkeypatch.setattr(main_mod, "proposal_store", test_store)
    monkeypatch.setattr(
        main_mod.repository_service,
        "proposal_revision",
        AsyncMock(return_value=("a" * 40, "b" * 40)),
    )

    proposal = test_store.create(
        repository="owner/repo",
        issue_number=1,
        target_branch="main",
        target_path="src/a.py",
        operation="modify",
        proposed_content="code",
        diff_preview={"reliable": True, "changed": True},
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        base_commit_sha="a" * 40,
        target_file_blob_sha="b" * 40,
    )
    client = TestClient(main_mod.app)

    # 1. No auth header -> 401
    r = client.post(f"/proposals/{proposal.proposal_id}/write")
    assert r.status_code == 401

    # 2. Invalid auth header -> 401
    r = client.post(
        f"/proposals/{proposal.proposal_id}/write",
        headers={"Authorization": "Bearer bad-token"},
    )
    assert r.status_code == 401

    # 3. Reader token -> 403
    r = client.post(
        f"/proposals/{proposal.proposal_id}/write",
        headers={"Authorization": "Bearer reader-tok"},
    )
    assert r.status_code == 403

    # 4. Approver token when GITHUB_WRITES_ENABLED=False -> 503
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", False)
    r = client.post(
        f"/proposals/{proposal.proposal_id}/write",
        headers={"Authorization": "Bearer approver-tok"},
    )
    assert r.status_code == 503
    assert "disabled" in r.json()["detail"].lower()

    # 5. Non-existent proposal -> 404 (when flag enabled)
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    r = client.post(
        "/proposals/non-existent-id/write",
        headers={"Authorization": "Bearer approver-tok"},
    )
    assert r.status_code == 404

    # 6. Unapproved proposal -> 409
    r = client.post(
        f"/proposals/{proposal.proposal_id}/write",
        headers={"Authorization": "Bearer approver-tok"},
    )
    assert r.status_code == 409


def test_write_endpoint_success_mocked(tmp_path, monkeypatch):
    tokens = {
        "approver-tok": {"subject": "approver@test", "roles": [PROPOSAL_APPROVER]},
    }
    monkeypatch.setenv("APPROVAL_AUTH_TOKENS", json.dumps(tokens))
    import backend.main as main_mod

    test_store = ProposalStore(tmp_path / "proposals.sqlite")
    monkeypatch.setattr(main_mod, "proposal_store", test_store)

    proposal = test_store.create(
        repository="owner/repo",
        issue_number=1,
        target_branch="main",
        target_path="src/a.py",
        operation="modify",
        proposed_content="code",
        diff_preview={"reliable": True, "changed": True},
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        base_commit_sha="a" * 40,
        target_file_blob_sha="b" * 40,
    )
    test_store.approve(proposal.proposal_id, proposal.content_digest, "approver@test")

    mock_orch = AsyncMock()
    mock_orch.execute.return_value = {
        "proposal": test_store.get(proposal.proposal_id),
        "commit": {"commit_sha": "c" * 40, "branch": "devsquad/proposal-x"},
        "pull_request": {
            "number": 12,
            "html_url": "https://github.com/owner/repo/pull/12",
        },
    }
    monkeypatch.setattr(main_mod, "get_proposal_write_orchestrator", lambda: mock_orch)
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)

    client = TestClient(main_mod.app)
    r = client.post(
        f"/proposals/{proposal.proposal_id}/write",
        headers={"Authorization": "Bearer approver-tok"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert data["commit"]["commit_sha"] == "c" * 40
    assert data["pull_request"]["number"] == 12


def test_write_endpoint_rate_limited(tmp_path, monkeypatch):
    tokens = {
        "approver-tok": {"subject": "spammer@test", "roles": [PROPOSAL_APPROVER]},
    }
    monkeypatch.setenv("APPROVAL_AUTH_TOKENS", json.dumps(tokens))
    import backend.main as main_mod

    test_store = ProposalStore(tmp_path / "proposals.sqlite")
    monkeypatch.setattr(main_mod, "proposal_store", test_store)
    monkeypatch.setattr(
        main_mod.repository_service,
        "proposal_revision",
        AsyncMock(return_value=("a" * 40, "b" * 40)),
    )

    proposal = test_store.create(
        repository="owner/repo",
        issue_number=1,
        target_branch="main",
        target_path="src/a.py",
        operation="modify",
        proposed_content="code",
        diff_preview={"reliable": True, "changed": True},
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        base_commit_sha="a" * 40,
        target_file_blob_sha="b" * 40,
    )
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)
    main_mod.approval_rate_limiter.reset()

    client = TestClient(main_mod.app)
    # Exhaust rate limiter (max 5 attempts per window)
    for _ in range(5):
        r = client.post(
            f"/proposals/{proposal.proposal_id}/write",
            headers={"Authorization": "Bearer approver-tok"},
        )
        assert r.status_code == 409  # not approved

    # 6th attempt should be blocked by rate limiter with 429
    r6 = client.post(
        f"/proposals/{proposal.proposal_id}/write",
        headers={"Authorization": "Bearer approver-tok"},
    )
    assert r6.status_code == 429
    assert "Retry-After" in r6.headers


def test_secret_redaction_in_github_error():
    error_msg = (
        "Error connecting to https://api.github.com with token "
        "ghp_ABCDEF1234567890abcdefghijklmnopqrstuvwxyz and key gsk_1234567890abcdef"
    )
    sanitized = sanitize_secret(error_msg)
    assert "ghp_ABCDEF1234567890abcdefghijklmnopqrstuvwxyz" not in sanitized
    assert "[REDACTED_GITHUB_TOKEN]" in sanitized
    assert "gsk_1234567890abcdef" not in sanitized
    assert "[REDACTED_GROQ_KEY]" in sanitized


def test_dry_run_workflow_never_invokes_write_methods():
    from backend.main import github_client

    assert settings.GITHUB_WRITES_ENABLED is False
    with pytest.raises(GitHubWritesDisabledError):
        import asyncio

        asyncio.run(github_client.create_branch("owner/repo", "b1", "main"))
    with pytest.raises(GitHubWritesDisabledError):
        import asyncio

        asyncio.run(
            github_client.commit_files("owner/repo", "b1", {"f.py": "x"}, "msg")
        )
    with pytest.raises(GitHubWritesDisabledError):
        import asyncio

        asyncio.run(
            github_client.create_pull_request(
                "owner/repo", "title", "b1", "main", "body"
            )
        )
