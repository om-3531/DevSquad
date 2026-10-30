
import asyncio
import logging
import random
import httpx

from backend.config import settings
from backend.github.validation import (
    validate_file_path,
    validate_git_ref,
    validate_repository,
    validate_sha,
)

logger = logging.getLogger("backend.github.client")

RETRYABLE_EXCEPTIONS = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.CloseError,
    httpx.NetworkError,
)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class GitHubWritesDisabledError(PermissionError):
    """Raised before any GitHub write request when the feature is disabled."""


class GitHubStaleRevisionError(RuntimeError):
    """Raised when a remote revision differs from the reviewed revision."""


class GitHubClient:
    def __init__(self):
        self.base_url = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "DevSquad/0.1.0",
        }

        if settings.GITHUB_TOKEN:
            self.headers["Authorization"] = (
                f"Bearer {settings.GITHUB_TOKEN}"
            )

    @staticmethod
    def _require_writes_enabled() -> None:
        if not settings.GITHUB_WRITES_ENABLED:
            raise GitHubWritesDisabledError("GitHub writes are disabled.")

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json_data: object = None,
        params: dict | None = None,
        timeout: float = 15.0,
        max_retries: int = 3,
        base_backoff: float = 0.5,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    if method.upper() == "GET":
                        response = await client.get(url, headers=self.headers, params=params)
                    elif method.upper() == "POST":
                        response = await client.post(url, headers=self.headers, json=json_data, params=params)
                    elif method.upper() == "PATCH":
                        response = await client.patch(url, headers=self.headers, json=json_data, params=params)
                    elif method.upper() == "PUT":
                        response = await client.put(url, headers=self.headers, json=json_data, params=params)
                    else:
                        response = await client.request(method, url, headers=self.headers, json=json_data, params=params)

                if response.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                    retry_after_str = response.headers.get("Retry-After")
                    delay = base_backoff * (2 ** attempt) + random.uniform(0.01, 0.05)
                    if retry_after_str:
                        try:
                            delay = max(delay, float(retry_after_str))
                        except ValueError:
                            pass
                    delay = min(delay, 10.0)
                    logger.warning(
                        "GitHub API returned HTTP %s on %s %s. Retrying in %.2fs (attempt %d/%d)...",
                        response.status_code, method, url, delay, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response

            except RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                if attempt < max_retries:
                    delay = min(base_backoff * (2 ** attempt) + random.uniform(0.01, 0.05), 10.0)
                    logger.warning(
                        "GitHub API transient error %s on %s %s. Retrying in %.2fs (attempt %d/%d)...",
                        type(exc).__name__, method, url, delay, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "GitHub API retries exhausted for %s %s after %d attempts: %s",
                        method, url, max_retries + 1, exc,
                    )
                    raise
            except httpx.HTTPStatusError:
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError("Unexpected retry loop termination.")

    async def get_issue(self, repo: str, issue_number: int):
        validate_repository(repo)
        if not isinstance(issue_number, int) or issue_number < 1:
            raise ValueError("Invalid GitHub issue number.")
        url = f"{self.base_url}/repos/{repo}/issues/{issue_number}"
        response = await self._request_with_retry("GET", url, timeout=15.0)
        return response.json()

    async def create_issue(
        self,
        repo: str,
        title: str,
        body: str | None = None,
    ) -> dict:
        self._require_writes_enabled()
        validate_repository(repo)

        if not isinstance(title, str) or not title.strip():
            raise ValueError("Issue title cannot be empty.")

        url = f"{self.base_url}/repos/{repo}/issues"
        payload = {"title": title.strip()}

        if body is not None:
            payload["body"] = body

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()

            if response.status_code != 201:
                raise RuntimeError(
                    f"Unexpected GitHub API response status: HTTP {response.status_code}"
                )

            return response.json()

    async def get_issues(
        self,
        repo: str,
        state: str = "open",
        filter_pull_requests: bool = True,
    ) -> list[dict]:
        validate_repository(repo)
        url = f"{self.base_url}/repos/{repo}/issues"
        response = await self._request_with_retry("GET", url, params={"state": state}, timeout=15.0)
        data = response.json()
        if filter_pull_requests and isinstance(data, list):
            return [item for item in data if "pull_request" not in item]
        return data

    async def get_repository(self, repo: str) -> dict:
        validate_repository(repo)
        url = f"{self.base_url}/repos/{repo}"
        response = await self._request_with_retry("GET", url, timeout=15.0)
        return response.json()

    async def get_branch_sha(self, repo: str, branch: str = "main"):
        validate_repository(repo)
        validate_git_ref(branch)
        url = f"{self.base_url}/repos/{repo}/git/ref/heads/{branch}"
        response = await self._request_with_retry("GET", url, timeout=15.0)
        return response.json()["object"]["sha"]

    async def create_branch(
        self,
        repo: str,
        branch_name: str,
        source_branch: str = "main",
        expected_source_sha: str | None = None,
    ):
        self._require_writes_enabled()
        validate_repository(repo)
        validate_git_ref(branch_name)
        validate_git_ref(source_branch)
        if branch_name == source_branch:
            raise ValueError("The new branch must differ from the source branch.")
        if expected_source_sha is None:
            raise ValueError("The reviewed source revision is required.")
        expected_source_sha = validate_sha(expected_source_sha)

        source_sha = await self.get_branch_sha(repo, source_branch)
        if source_sha.lower() != expected_source_sha:
            raise GitHubStaleRevisionError("The source branch changed after review.")
        url = f"{self.base_url}/repos/{repo}/git/refs"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url,
                    headers=self.headers,
                    json={
                        "ref": f"refs/heads/{branch_name}",
                        "sha": source_sha,
                    },
                )
                response.raise_for_status()
                return response.json()
        except RETRYABLE_EXCEPTIONS:
            # Reconcile remote state: check if branch was created despite the connection drop
            if await self.branch_exists(repo, branch_name):
                logger.info("Branch %s already created after transient error. Reconciled remote state.", branch_name)
                return {"ref": f"refs/heads/{branch_name}", "reconciled": True}
            raise

    async def get_file_blob_sha(self, repo: str, path: str, branch: str) -> str | None:
        """Read a file blob SHA, returning None only when the file is absent."""
        validate_repository(repo)
        validate_file_path(path)
        validate_git_ref(branch)
        url = f"{self.base_url}/repos/{repo}/contents/{path}"
        try:
            response = await self._request_with_retry("GET", url, params={"ref": branch}, timeout=15.0)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            raise
        data = response.json()
        sha = data.get("sha")
        return validate_sha(sha) if isinstance(sha, str) else None

    async def branch_exists(self, repo: str, branch: str) -> bool:
        validate_repository(repo)
        validate_git_ref(branch)
        url = f"{self.base_url}/repos/{repo}/git/ref/heads/{branch}"
        try:
            response = await self._request_with_retry("GET", url, timeout=15.0)
            return True
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return False
            raise

    async def ensure_branch(
        self,
        repo: str,
        branch_name: str,
        source_branch: str = "main",
        expected_source_sha: str | None = None,
    ):
        """
        Ensures a branch exists. If the branch does not exist (HTTP 404),
        it creates the branch from source_branch.
        On 401, 403, 429, 500 or any non-404 error, raises HTTPStatusError.
        """
        self._require_writes_enabled()
        validate_repository(repo)
        validate_git_ref(branch_name)
        validate_git_ref(source_branch)
        exists = await self.branch_exists(repo, branch_name)
        if not exists:
            return await self.create_branch(repo, branch_name, source_branch, expected_source_sha)
        return {"ref": f"refs/heads/{branch_name}", "exists": True}

    async def commit_files(
        self,
        repo: str,
        branch: str,
        files: dict[str, str],
        message: str,
        expected_branch_sha: str | None = None,
        expected_file_blob_shas: dict[str, str | None] | None = None,
    ):
        self._require_writes_enabled()
        validate_repository(repo)
        validate_git_ref(branch)
        if not files:
            raise ValueError("At least one file is required.")
        if branch == "main":
            raise ValueError("Changes must target a feature branch.")
        if not message.strip():
            raise ValueError("Commit message cannot be empty.")

        for path, content in files.items():
            try:
                validate_file_path(path)
            except ValueError:
                raise ValueError(f"Invalid file path or content: {path!r}")
            if not isinstance(content, str):
                raise ValueError(f"Invalid file path or content: {path!r}")
        if expected_branch_sha is None or expected_file_blob_shas is None:
            raise ValueError("Reviewed revision metadata is required.")
        expected_branch_sha = validate_sha(expected_branch_sha)
        if set(files) != set(expected_file_blob_shas):
            raise ValueError("Revision metadata must cover every committed file.")

        branch_sha = await self.get_branch_sha(repo, branch)
        if branch_sha.lower() != expected_branch_sha:
            raise GitHubStaleRevisionError("The target branch changed after review.")
        for path, expected_blob_sha in expected_file_blob_shas.items():
            if expected_blob_sha is not None:
                expected_blob_sha = validate_sha(expected_blob_sha)
            current_blob_sha = await self.get_file_blob_sha(repo, path, branch)
            if current_blob_sha != expected_blob_sha:
                raise GitHubStaleRevisionError("A target file changed after review.")
        commit_url = (
            f"{self.base_url}/repos/{repo}/git/commits/{branch_sha}"
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(commit_url, headers=self.headers)
            response.raise_for_status()
            base_commit = response.json()

            tree_entries = [
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "content": content,
                }
                for path, content in files.items()
            ]

            response = await client.post(
                f"{self.base_url}/repos/{repo}/git/trees",
                headers=self.headers,
                json={
                    "base_tree": base_commit["tree"]["sha"],
                    "tree": tree_entries,
                },
            )
            response.raise_for_status()
            new_tree = response.json()

            response = await client.post(
                f"{self.base_url}/repos/{repo}/git/commits",
                headers=self.headers,
                json={
                    "message": message,
                    "tree": new_tree["sha"],
                    "parents": [branch_sha],
                },
            )
            response.raise_for_status()
            new_commit = response.json()

            try:
                response = await client.patch(
                    f"{self.base_url}/repos/{repo}/git/refs/heads/{branch}",
                    headers=self.headers,
                    json={"sha": new_commit["sha"], "force": False},
                )
                response.raise_for_status()
            except RETRYABLE_EXCEPTIONS:
                # Reconcile: check if the branch ref was updated to the new commit SHA
                current_head = await self.get_branch_sha(repo, branch)
                if current_head.lower() == new_commit["sha"].lower():
                    logger.info("Ref heads/%s updated to %s despite transient error. Reconciled.", branch, new_commit["sha"])
                else:
                    raise

            return {
                "commit_sha": new_commit["sha"],
                "commit_url": new_commit["html_url"],
                "branch": branch,
                "files": list(files.keys()),
            }

    async def get_pull_request_by_head(self, repo: str, head: str) -> dict | None:
        """Find an existing open or closed pull request matching the head branch."""
        validate_repository(repo)
        validate_git_ref(head)
        url = f"{self.base_url}/repos/{repo}/pulls"
        try:
            owner = repo.split("/")[0]
            qualified_head = f"{owner}:{head}"
            response = await self._request_with_retry(
                "GET", url, params={"head": qualified_head, "state": "all"}, timeout=15.0
            )
            data = response.json()
            if isinstance(data, list) and data:
                return data[0]
            # Try unqualified head if owner-qualified search did not return a match
            response = await self._request_with_retry(
                "GET", url, params={"head": head, "state": "all"}, timeout=15.0
            )
            data = response.json()
            if isinstance(data, list) and data:
                return data[0]
            return None
        except Exception:
            return None

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
        draft: bool = True,
        expected_head_sha: str | None = None,
        expected_base_sha: str | None = None,
    ):
        self._require_writes_enabled()
        validate_repository(repo)
        validate_git_ref(head)
        validate_git_ref(base)
        if not title.strip():
            raise ValueError("Pull request title cannot be empty.")
        if not head or not base or head == base:
            raise ValueError("Valid, different head and base branches are required.")
        if expected_head_sha is None or expected_base_sha is None:
            raise ValueError("Reviewed branch revisions are required.")
        expected_head_sha = validate_sha(expected_head_sha)
        expected_base_sha = validate_sha(expected_base_sha)
        if (await self.get_branch_sha(repo, head)).lower() != expected_head_sha:
            raise GitHubStaleRevisionError("The pull request branch changed after review.")
        if (await self.get_branch_sha(repo, base)).lower() != expected_base_sha:
            raise GitHubStaleRevisionError("The pull request base changed after review.")

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.base_url}/repos/{repo}/pulls",
                    headers=self.headers,
                    json={
                        "title": title,
                        "head": head,
                        "base": base,
                        "body": body,
                        "draft": True,
                    },
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as error:
            # If GitHub returns 422 because PR already exists, reconcile remote state
            if error.response.status_code == 422:
                existing = await self.get_pull_request_by_head(repo, head)
                if existing:
                    logger.info("Found existing pull request for head %s after 422. Reconciled remote state.", head)
                    return existing
            raise
        except RETRYABLE_EXCEPTIONS:
            # If network failed ambiguously, check if PR was created
            existing = await self.get_pull_request_by_head(repo, head)
            if existing:
                logger.info("Found existing pull request for head %s after transient error. Reconciled remote state.", head)
                return existing
            raise
