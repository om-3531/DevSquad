from __future__ import annotations

from typing import Any

import httpx

from backend.github.client import GitHubClient
from backend.github.validation import validate_repository


class IssueService:
    def __init__(self, github_client: GitHubClient | None = None):
        self.github = (
            github_client
            if github_client is not None
            else GitHubClient()
        )

    async def get_issue(self, repo: str, issue_number: int):
        return await self.github.get_issue(
            repo=repo,
            issue_number=issue_number,
        )

    @staticmethod
    def _handle_http_error(status: int, repo: str, exc: Exception | None = None) -> None:
        if status == 404:
            raise FileNotFoundError(
                f"GitHub repository '{repo}' not found."
            ) from exc
        if status in (401, 403):
            raise PermissionError(
                "GitHub permission denied or unauthorized."
            ) from exc
        if status == 429:
            raise RuntimeError(
                "GitHub API rate limit exceeded. Please retry later."
            ) from exc
        if 500 <= status <= 599:
            raise RuntimeError(
                f"GitHub server error encountered (HTTP {status})."
            ) from exc
        raise RuntimeError(
            f"Unexpected GitHub API response status: HTTP {status}."
        ) from exc

    async def create_issue(
        self,
        repo: str,
        title: str,
        body: str | None = None,
    ) -> dict[str, int | str]:
        validate_repository(repo)

        if not isinstance(title, str) or not title.strip():
            raise ValueError("Issue title cannot be empty.")

        clean_title = title.strip()

        # Delegate directly to existing GitHubClient.create_issue()
        # This preserves:
        # - Write capability safety check (GitHubWritesDisabledError)
        # - Authentication headers and configuration
        # - Centralized HTTP client and request abstraction
        try:
            raw_issue = await self.github.create_issue(
                repo=repo,
                title=clean_title,
                body=body,
            )
        except httpx.HTTPStatusError as exc:
            self._handle_http_error(exc.response.status_code, repo, exc)

        if hasattr(raw_issue, "json") and callable(raw_issue.json):
            try:
                raw_issue = raw_issue.json()
            except Exception as exc:
                raise ValueError("Malformed GitHub API response: expected a dictionary.") from exc

        if not isinstance(raw_issue, dict):
            raise ValueError(
                "Malformed GitHub API response: expected a dictionary."
            )

        issue_id = raw_issue.get("id")
        html_url = raw_issue.get("html_url")

        if (
            not isinstance(issue_id, int)
            or isinstance(issue_id, bool)
            or not isinstance(html_url, str)
            or not html_url.strip()
        ):
            raise ValueError(
                "Malformed GitHub API response: missing or invalid "
                "'id' or 'html_url'."
            )

        return {
            "id": issue_id,
            "html_url": html_url,
        }


async def create_test_issue(
    repo: str,
    title: str = "Test Issue",
    body: str | None = None,
    client: GitHubClient | None = None,
    github_client: GitHubClient | None = None,
    **kwargs: Any,
) -> str:
    """Convenience wrapper delegating through IssueService and returning the issue URL."""
    injected_client = client if client is not None else (github_client or kwargs.get("client") or kwargs.get("github_client"))
    service = IssueService(github_client=injected_client)
    desc = body if body is not None else kwargs.get("description")
    result = await service.create_issue(
        repo=repo,
        title=title,
        body=desc,
    )
    return str(result["html_url"])