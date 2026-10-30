"""Dedicated Live GitHub Integration Test for DevSquad.

SAFETY GUARDS:
1. This test NEVER runs during ordinary automated CI or test runs.
2. It requires explicit operator authorization via the environment variable:
     ENABLE_LIVE_GITHUB_TEST=true
3. It requires GITHUB_WRITES_ENABLED=true, a valid GITHUB_TOKEN, and a dedicated GITHUB_REPO.
4. It does not perform destructive actions: it creates a unique test branch,
   commits a non-destructive verification file, creates a DRAFT Pull Request,
   and verifies the remote state via GitHub API.
"""

from __future__ import annotations

import os
import time
import uuid
import pytest
from datetime import UTC, datetime

from backend.config import settings
from backend.github.client import GitHubClient, GitHubWritesDisabledError
from backend.github.repository import RepositoryService
from backend.github.write_orchestrator import ProposalWriteOrchestrator
from backend.proposals import ProposalStore, APPROVED, PR_CREATED
from backend.diff_preview import build_diff_preview


LIVE_TEST_ENABLED = os.getenv("ENABLE_LIVE_GITHUB_TEST", "false").strip().lower() == "true"


@pytest.mark.skipif(
    not LIVE_TEST_ENABLED,
    reason="Live GitHub integration test requires explicit operator opt-in via ENABLE_LIVE_GITHUB_TEST=true.",
)
@pytest.mark.asyncio
async def test_live_github_branch_commit_and_draft_pr(tmp_path):
    """
    Executes a real, end-to-end GitHub mutation against an authorized dedicated test repository.
    Verifies:
      1. Safe branch creation from default branch.
      2. Multi-file or single-file commit creation containing approved changes.
      3. Draft Pull Request creation targeting default branch.
      4. Verification via real GitHub API responses.
      5. Durable persistence of branch, commit SHA, PR number, and PR URL.
    """
    if not settings.GITHUB_WRITES_ENABLED:
        pytest.fail(
            "GITHUB_WRITES_ENABLED is false. Operator must explicitly configure "
            "GITHUB_WRITES_ENABLED=true in the environment to run live integration tests."
        )

    if not settings.GITHUB_TOKEN or not settings.GITHUB_TOKEN.strip():
        pytest.fail("GITHUB_TOKEN is missing or empty. A valid token is required for live testing.")

    # Dedicated test repository: strictly locked to om-3531/devsquad-test
    ALLOWED_TEST_REPO = "om-3531/devsquad-test"
    configured_repo = (os.getenv("GITHUB_TEST_REPO") or "om-3531/devsquad-test").strip()

    # Strict protection against mutating the main project repository
    if configured_repo.lower() in {"om-3531/devsquad", "om-3531/devsquad.git"}:
        pytest.fail(
            "Accidental write protection: Live integration tests are strictly forbidden from targeting the main "
            "repository 'om-3531/DevSquad'. A dedicated test repository such as 'om-3531/devsquad-test' must be used."
        )

    # Ensure the test cannot accidentally target any repository other than om-3531/devsquad-test
    if configured_repo.lower() != ALLOWED_TEST_REPO.lower():
        pytest.fail(
            f"Safety restriction: Live integration test is strictly restricted to target only '{ALLOWED_TEST_REPO}', "
            f"got '{configured_repo}'."
        )

    repo = ALLOWED_TEST_REPO

    # 1. Initialize services with isolated database
    test_db = tmp_path / "live_test_proposals.sqlite3"
    store = ProposalStore(test_db)
    github = GitHubClient()
    repository_service = RepositoryService(github_client=github)

    # 2. Check repository read access and default branch
    repo_info = await repository_service.get_repository(repo)
    default_branch = repo_info.get("default_branch") or "main"
    try:
        base_commit_sha = await repository_service.get_branch_sha(repo, default_branch)
    except Exception as exc:
        pytest.fail(
            f"Failed to read base commit SHA for {repo}:{default_branch} ({exc}). "
            f"The repository is uninitialized (empty). Please create an initial commit (e.g. README.md) "
            f"on '{default_branch}' before running live integration tests."
        )
    assert base_commit_sha and len(base_commit_sha) == 40, f"Failed to get valid base SHA for {repo}:{default_branch}"

    # 3. Formulate unique non-conflicting safe target smoke test file
    unique_suffix = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
    target_path = f"tests/live_smoke_{unique_suffix}.py"
    proposed_content = (
        f"# DevSquad Live Integration Verification\n"
        f"# Run timestamp: {datetime.now(UTC).isoformat()}\n"
        f"# Unique run ID: {unique_suffix}\n\n"
        f"def test_devsquad_live_smoke():\n"
        f"    assert True\n"
    )

    diff_preview = build_diff_preview(
        path=target_path,
        operation="create",
        proposed_content=proposed_content,
        original_content="",
        original_content_available=True,
    )

    # 4. Create and approve the proposal
    proposal = store.create(
        repository=repo,
        issue_number=9999,
        target_branch=default_branch,
        target_path=target_path,
        operation="create",
        proposed_content=proposed_content,
        diff_preview=diff_preview,
        quality_gate_status="REVIEW_APPROVED_GENERATED_TESTS_PASSED",
        base_commit_sha=base_commit_sha,
        target_file_blob_sha=None,
    )

    digest = ProposalStore.digest(proposed_content)
    approved_proposal = store.approve(
        proposal.proposal_id,
        digest,
        approved_by="authorized_operator",
    )
    assert approved_proposal.status == APPROVED

    # 5. Execute write orchestration
    orchestrator = ProposalWriteOrchestrator(store, repository_service, github)
    result = await orchestrator.execute(proposal.proposal_id, "authorized_operator")

    assert result["proposal"].status == PR_CREATED
    commit_sha = result["commit"]["commit_sha"]
    pr_number = result["pull_request"]["number"]
    pr_url = result["pull_request"]["html_url"]
    write_branch = result["commit"]["branch"]

    print(f"\n[LIVE TEST RESULT] Branch: {write_branch}")
    print(f"[LIVE TEST RESULT] Commit SHA: {commit_sha}")
    print(f"[LIVE TEST RESULT] File: {target_path}")
    print(f"[LIVE TEST RESULT] Draft PR Number: #{pr_number}")
    print(f"[LIVE TEST RESULT] Draft PR URL: {pr_url}")

    assert len(commit_sha) == 40
    assert pr_number > 0
    assert "github.com" in pr_url

    # 6. Verify remote state via GitHub API
    remote_branch_sha = await github.get_branch_sha(repo, result["commit"]["branch"])
    assert remote_branch_sha.lower() == commit_sha.lower(), "Remote branch SHA does not match committed SHA"

    remote_file_sha = await github.get_file_blob_sha(repo, target_path, result["commit"]["branch"])
    assert remote_file_sha is not None, f"File {target_path} not found on remote branch"

    # Verify persisted proposal record
    persisted = store.get(proposal.proposal_id)
    assert persisted is not None
    assert persisted.status == PR_CREATED
    assert persisted.commit_sha == commit_sha
    assert persisted.pr_number == pr_number
    assert persisted.pr_url == pr_url
