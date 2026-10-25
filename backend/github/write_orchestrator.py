"""Controlled, default-disabled orchestration for approved GitHub proposals."""

from __future__ import annotations

from backend.config import settings
from backend.github.client import GitHubStaleRevisionError, GitHubWritesDisabledError, GitHubClient
from backend.github.repository import RepositoryService
from backend.github.validation import validate_file_path, validate_git_ref, validate_repository
from backend.proposals import ProposalStore, StaleProposalError


class ProposalWriteOrchestrator:
    def __init__(self, store: ProposalStore, repository: RepositoryService, github: GitHubClient):
        self.store = store
        self.repository = repository
        self.github = github

    async def execute(self, proposal_id: str, actor_subject: str) -> dict:
        # This check intentionally happens before *any* remote request.
        if not settings.GITHUB_WRITES_ENABLED:
            raise GitHubWritesDisabledError("GitHub writes are disabled.")

        proposal = self.store.get(proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)

        if not proposal.target_path:
            raise ValueError("Proposal target path is required for writing changes.")
        validate_repository(proposal.repository)
        validate_git_ref(proposal.target_branch)
        validate_file_path(proposal.target_path)
        feature_branch = validate_git_ref(f"devsquad/proposal-{proposal.proposal_id}")

        # Read-only revision check before claiming or mutating remote state.
        current_branch_sha, current_file_sha = await self.repository.proposal_revision(proposal)
        self.store.verify_revision(proposal_id, current_branch_sha, current_file_sha)
        proposal = self.store.begin_write(proposal_id, actor_subject, feature_branch)
        try:
            already_committed = bool(proposal.commit_sha and proposal.write_branch)

            if not already_committed:
                # Recheck after the concurrency claim. GitHub's non-force ref update
                # remains the final compare-and-swap guard against a later race.
                current_branch_sha, current_file_sha = await self.repository.proposal_revision(proposal)
                self.store.verify_revision(proposal_id, current_branch_sha, current_file_sha)
                await self.github.create_branch(
                    proposal.repository, feature_branch, proposal.target_branch,
                    proposal.base_commit_sha,
                )
                commit = await self.github.commit_files(
                    proposal.repository,
                    feature_branch,
                    {proposal.target_path: proposal.proposed_content},
                    f"devsquad: proposal {proposal.proposal_id}",
                    proposal.base_commit_sha,
                    {proposal.target_path: proposal.target_file_blob_sha},
                )
                commit_sha = commit["commit_sha"]
                proposal = self.store.record_commit(proposal_id, actor_subject, commit_sha)
            else:
                commit_sha = proposal.commit_sha
                feature_branch = proposal.write_branch
                commit = {
                    "commit_sha": commit_sha,
                    "commit_url": f"https://github.com/{proposal.repository}/commit/{commit_sha}",
                    "branch": feature_branch,
                    "files": [proposal.target_path],
                }

            pull_request = await self.github.create_pull_request(
                proposal.repository,
                f"Draft: proposal {proposal.proposal_id}",
                feature_branch,
                proposal.target_branch,
                f"DevSquad proposal {proposal.proposal_id}.\n\nReviewed candidate; draft PR for human review.",
                True,
                commit_sha,
                proposal.base_commit_sha,
            )
            proposal = self.store.record_pull_request(
                proposal_id, actor_subject, pull_request["number"], pull_request["html_url"],
            )
            return {"proposal": proposal, "commit": commit, "pull_request": pull_request}
        except Exception:
            try:
                self.store.record_write_failure(proposal_id, actor_subject)
            except ValueError:
                # A stale check may already have transitioned the proposal.
                pass
            raise

