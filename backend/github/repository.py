
import base64
import httpx

from backend.github.client import GitHubClient
from backend.github.validation import validate_file_path, validate_git_ref, validate_repository


class RepositoryService:
    def __init__(self, github_client: GitHubClient | None = None):
        self.github = github_client or GitHubClient()

    async def get_branch_sha(self, repo: str, branch: str) -> str:
        """Return the immutable commit SHA used to build a reviewed proposal."""
        return await self.github.get_branch_sha(repo, branch)

    async def proposal_revision(self, proposal) -> tuple[str, str | None]:
        """Read the remote revisions required before a future write is attempted."""
        branch_sha = await self.get_branch_sha(proposal.repository, proposal.target_branch)
        file_sha = None
        if proposal.target_path:
            try:
                file_sha = (await self.get_file(
                    proposal.repository,
                    proposal.target_path,
                    proposal.target_branch,
                )).get("sha")
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 404:
                    raise
        return branch_sha, file_sha

    async def get_file(
        self,
        repo: str,
        path: str,
        branch: str = "main",
    ):
        validate_repository(repo)
        validate_file_path(path)
        validate_git_ref(branch)
        url = (
            f"{self.github.base_url}/repos/{repo}/contents/{path}"
            f"?ref={branch}"
        )

        response = await self.github._request_with_retry(
            "GET",
            url,
            timeout=15.0,
        )
        data = response.json()

        if data.get("encoding") == "base64" and data.get("content"):
            decoded_content = base64.b64decode(
                data["content"]
            ).decode("utf-8")

            data["decoded_content"] = decoded_content
            data.pop("content", None)

        return data

    async def get_repository(self, repo: str):
        validate_repository(repo)
        url = f"{self.github.base_url}/repos/{repo}"

        response = await self.github._request_with_retry("GET", url, timeout=15.0)
        data = response.json()

        return {
            "name": data.get("name"),
            "full_name": data.get("full_name"),
            "description": data.get("description"),
            "default_branch": data.get("default_branch"),
            "language": data.get("language"),
            "html_url": data.get("html_url"),
            "private": data.get("private"),
        }

    async def get_tree(self, repo: str, branch: str = "main") -> list[str]:
        """Fetch repository file paths, excluding hidden, venv, and binary/build paths."""
        validate_repository(repo)
        validate_git_ref(branch)
        url = (
            f"{self.github.base_url}/repos/{repo}/git/trees/{branch}"
            "?recursive=1"
        )

        response = await self.github._request_with_retry("GET", url, timeout=15.0)
        data = response.json()

        ignored_prefixes = (
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            "node_modules",
            "dist",
            ".pytest_cache",
        )

        paths = []
        for item in data.get("tree", []):
            if item.get("type") == "blob":
                path = item.get("path", "")
                if path and not any(part.startswith(".") for part in path.split("/")) and not path.startswith(ignored_prefixes):
                    paths.append(path)

        return sorted(paths)
