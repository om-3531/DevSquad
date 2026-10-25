from __future__ import annotations

import httpx

from backend.github.client import GitHubClient
from backend.github.validation import validate_repository


class IssueService:
    def __init__(self, github_client: GitHubClient | None = None):
        self.github = github_client if github_client is not None else GitHubClient()

    async def get_issue(self, repo: str, issue_number: int):
        return await self.github.get_issue(
            repo=repo,
            issue_number=issue_number,
        )

    async def create_issue(
        self,
        repo: str,
        title: str,
        body: str | None = None,
    ) -> dict[str, int | str]:
        validate_repository(repo)
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Issue title cannot be empty.")

        url = f"{self.github.base_url}/repos/{repo}/issues"
        payload: dict[str, str] = {"title": title.strip()}
        if body is not None:
            payload["body"] = body

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                headers=self.github.headers,
                json=payload,
            )

        status = response.status_code
        if status == 201:
            try:
                data = response.json()
            except Exception as exc:
                raise ValueError("Malformed GitHub API response: response body is not valid JSON.") from exc

            if not isinstance(data, dict):
                raise ValueError("Malformed GitHub API response: expected JSON object.")

            issue_id = data.get("id")
            html_url = data.get("html_url")

            if not isinstance(issue_id, int) or not isinstance(html_url, str) or not html_url.strip():
                raise ValueError("Malformed GitHub API response: missing or invalid 'id' or 'html_url'.")

            return {
                "id": issue_id,
                "html_url": html_url,
            }

        if status == 404:
            raise FileNotFoundError(f"GitHub repository '{repo}' not found.")
        if status in (401, 403):
            raise PermissionError(f"GitHub permission denied or unauthorized for repository '{repo}' (HTTP {status}).")
        if status == 429:
            raise RuntimeError(f"GitHub API rate limit exceeded (HTTP 429). Please retry later.")
        if 500 <= status <= 599:
            raise RuntimeError(f"GitHub server error encountered (HTTP {status}).")

        raise RuntimeError(f"Unexpected GitHub API response status: HTTP {status}.")
