
import httpx
import pytest
from unittest.mock import AsyncMock

from backend.github.client import GitHubClient, GitHubWritesDisabledError
from backend.config import settings
from backend.github.repository import RepositoryService
from backend.github.issues import IssueService


@pytest.fixture(autouse=True)
def mocked_write_capability(monkeypatch):
    """Existing write-client unit tests use mocks only; production remains off."""
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", True)


@pytest.mark.asyncio
async def test_get_branch_sha(monkeypatch):
    async def mock_get(self, url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=request,
            json={"object": {"sha": "abc123"}},
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    client = GitHubClient()
    sha = await client.get_branch_sha("om-3531/DevSquad", "main")

    assert sha == "abc123"


@pytest.mark.asyncio
async def test_create_branch(monkeypatch):
    async def mock_get_branch_sha(self, repo, branch="main"):
        assert repo == "om-3531/DevSquad"
        assert branch == "main"
        return "a" * 40

    async def mock_post(self, url, **kwargs):
        assert url.endswith("/git/refs")
        assert kwargs["json"] == {
            "ref": "refs/heads/devsquad/test-branch",
            "sha": "a" * 40,
        }
        request = httpx.Request("POST", url)
        return httpx.Response(
            201,
            request=request,
            json={"ref": "refs/heads/devsquad/test-branch"},
        )

    monkeypatch.setattr(
        GitHubClient, "get_branch_sha", mock_get_branch_sha
    )
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    client = GitHubClient()
    result = await client.create_branch(
        "om-3531/DevSquad",
        "devsquad/test-branch",
        expected_source_sha="a" * 40,
    )

    assert result["ref"] == "refs/heads/devsquad/test-branch"


@pytest.mark.asyncio
async def test_create_pull_request(monkeypatch):
    async def mock_get_branch_sha(self, repo, branch):
        return "a" * 40 if branch == "devsquad/test-branch" else "b" * 40

    async def mock_post(self, url, **kwargs):
        assert url.endswith("/pulls")
        assert kwargs["json"]["head"] == "devsquad/test-branch"
        assert kwargs["json"]["base"] == "main"
        assert kwargs["json"]["draft"] is True

        request = httpx.Request("POST", url)
        return httpx.Response(
            201,
            request=request,
            json={
                "number": 42,
                "html_url": (
                    "https://github.com/om-3531/DevSquad/pull/42"
                ),
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    monkeypatch.setattr(GitHubClient, "get_branch_sha", mock_get_branch_sha)

    client = GitHubClient()
    result = await client.create_pull_request(
        repo="om-3531/DevSquad",
        title="Test pull request",
        head="devsquad/test-branch",
        body="Automated test",
        expected_head_sha="a" * 40,
        expected_base_sha="b" * 40,
    )

    assert result["number"] == 42


@pytest.mark.asyncio
async def test_commit_files_rejects_main_branch():
    client = GitHubClient()

    with pytest.raises(ValueError, match="feature branch"):
        await client.commit_files(
            repo="om-3531/DevSquad",
            branch="main",
            files={"example.py": "print('hello')"},
            message="Test commit",
        )


@pytest.mark.asyncio
async def test_commit_files_rejects_invalid_path():
    client = GitHubClient()

    with pytest.raises(ValueError, match="Invalid file path"):
        await client.commit_files(
            repo="om-3531/DevSquad",
            branch="devsquad/test-branch",
            files={"../outside.py": "print('hello')"},
            message="Test commit",
        )

        
@pytest.mark.asyncio
async def test_commit_files_success(monkeypatch):
    client = GitHubClient()

    async def mock_get_branch_sha(self, repo, branch="main"):
        assert repo == "om-3531/DevSquad"
        assert branch == "devsquad/test-branch"
        return "a" * 40

    responses = [
        {"sha": "b" * 40},
        {
            "tree": {"sha": "base-tree-sha"},
        },
        {
            "sha": "new-tree-sha",
        },
        {
            "sha": "new-commit-sha",
            "html_url": "https://github.com/om-3531/DevSquad/commit/new-commit-sha",
        },
    ]

    async def mock_get(self, url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, request=request, json=responses.pop(0))

    async def mock_post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(201, request=request, json=responses.pop(0))

    async def mock_patch(self, url, **kwargs):
        assert url.endswith("/git/refs/heads/devsquad/test-branch")
        assert kwargs["json"]["sha"] == "new-commit-sha"
        request = httpx.Request("PATCH", url)
        return httpx.Response(200, request=request, json={"ref": "updated"})

    monkeypatch.setattr(
        GitHubClient, "get_branch_sha", mock_get_branch_sha
    )
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    monkeypatch.setattr(httpx.AsyncClient, "patch", mock_patch)

    result = await client.commit_files(
        repo="om-3531/DevSquad",
        branch="devsquad/test-branch",
        files={"example.py": "print('hello')"},
        message="Add example file",
        expected_branch_sha="a" * 40,
        expected_file_blob_shas={"example.py": "b" * 40},
    )

    assert result["commit_sha"] == "new-commit-sha"
    assert result["branch"] == "devsquad/test-branch"
    assert result["files"] == ["example.py"]


@pytest.mark.asyncio
async def test_branch_exists_true(monkeypatch):
    async def mock_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(200, request=req, json={"ref": "refs/heads/feature"})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    client = GitHubClient()
    exists = await client.branch_exists("om-3531/DevSquad", "feature")
    assert exists is True


@pytest.mark.asyncio
async def test_branch_exists_false_on_404(monkeypatch):
    async def mock_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(404, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    client = GitHubClient()
    exists = await client.branch_exists("om-3531/DevSquad", "non-existent")
    assert exists is False


@pytest.mark.asyncio
async def test_branch_exists_raises_on_500(monkeypatch):
    async def mock_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(500, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    client = GitHubClient()
    with pytest.raises(httpx.HTTPStatusError):
        await client.branch_exists("om-3531/DevSquad", "feature")


@pytest.mark.asyncio
async def test_ensure_branch_when_already_exists(monkeypatch):
    client = GitHubClient()

    async def mock_branch_exists(self, repo, branch):
        return True

    monkeypatch.setattr(GitHubClient, "branch_exists", mock_branch_exists)
    result = await client.ensure_branch("om-3531/DevSquad", "existing-branch")
    assert result["exists"] is True


@pytest.mark.asyncio
async def test_ensure_branch_creates_after_genuine_404(monkeypatch):
    client = GitHubClient()

    async def mock_branch_exists(self, repo, branch):
        return False

    async def mock_create_branch(self, repo, branch_name, source_branch="main", expected_source_sha=None):
        return {"ref": f"refs/heads/{branch_name}", "created": True}

    monkeypatch.setattr(GitHubClient, "branch_exists", mock_branch_exists)
    monkeypatch.setattr(GitHubClient, "create_branch", mock_create_branch)

    result = await client.ensure_branch("om-3531/DevSquad", "new-branch")
    assert result["created"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
async def test_ensure_branch_does_not_create_on_non_404_errors(monkeypatch, status_code):
    client = GitHubClient()

    async def mock_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(status_code, request=req)

    async def mock_create_branch(self, repo, branch_name, source_branch="main", expected_source_sha=None):
        raise AssertionError("create_branch must not be called on error status")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    monkeypatch.setattr(GitHubClient, "create_branch", mock_create_branch)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.ensure_branch("om-3531/DevSquad", "new-branch")
    assert exc_info.value.response.status_code == status_code


@pytest.mark.asyncio
async def test_write_capability_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", False)
    client = GitHubClient()

    with pytest.raises(PermissionError, match="writes are disabled"):
        await client.create_pull_request("owner/repo", "title", "feature", "main")


@pytest.mark.asyncio
async def test_commit_files_rejects_stale_branch_without_write(monkeypatch):
    client = GitHubClient()
    post = AsyncMock(side_effect=AssertionError("write request must not be made"))

    async def branch_sha(self, repo, branch):
        return "b" * 40

    monkeypatch.setattr(GitHubClient, "get_branch_sha", branch_sha)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    with pytest.raises(RuntimeError, match="branch changed"):
        await client.commit_files("owner/repo", "feature", {"src/a.py": "x"}, "message", "a" * 40, {"src/a.py": "c" * 40})
    post.assert_not_called()


@pytest.mark.asyncio
async def test_commit_files_rejects_stale_file_without_write(monkeypatch):
    client = GitHubClient()
    post = AsyncMock(side_effect=AssertionError("write request must not be made"))

    async def branch_sha(self, repo, branch):
        return "a" * 40

    async def file_sha(self, repo, path, branch):
        return "d" * 40

    monkeypatch.setattr(GitHubClient, "get_branch_sha", branch_sha)
    monkeypatch.setattr(GitHubClient, "get_file_blob_sha", file_sha)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    with pytest.raises(RuntimeError, match="file changed"):
        await client.commit_files("owner/repo", "feature", {"src/a.py": "x"}, "message", "a" * 40, {"src/a.py": "c" * 40})
    post.assert_not_called()


@pytest.mark.asyncio
async def test_get_issue_success(monkeypatch):
    client = GitHubClient()

    async def mock_get(self, url, **kwargs):
        assert url == "https://api.github.com/repos/om-3531/DevSquad/issues/1"
        assert kwargs["headers"]["Accept"] == "application/vnd.github+json"
        assert kwargs["headers"]["User-Agent"] == "DevSquad/0.1.0"
        assert self.timeout.read == 15.0
        req = httpx.Request("GET", url)
        return httpx.Response(200, request=req, json={"number": 1, "title": "Test Issue"})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    result = await client.get_issue("om-3531/DevSquad", 1)
    assert result == {"number": 1, "title": "Test Issue"}


@pytest.mark.asyncio
async def test_get_issues_success_and_request_attributes(monkeypatch):
    client = GitHubClient()
    calls = []

    async def mock_get(self, url, **kwargs):
        calls.append({
            "url": str(url),
            "headers": kwargs.get("headers"),
            "params": kwargs.get("params"),
            "timeout": self.timeout.read,
        })
        req = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=req,
            json=[
                {"id": 1, "number": 10, "title": "First Issue"},
                {"id": 2, "number": 11, "title": "Second Issue"},
            ],
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    # 1. Test default parameters
    issues = await client.get_issues("om-3531/DevSquad")
    assert len(issues) == 2
    assert issues[0]["title"] == "First Issue"
    assert calls[0]["url"] == "https://api.github.com/repos/om-3531/DevSquad/issues"
    assert calls[0]["headers"]["Accept"] == "application/vnd.github+json"
    assert calls[0]["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    assert calls[0]["headers"]["User-Agent"] == "DevSquad/0.1.0"
    assert calls[0]["params"] == {"state": "open"}
    assert calls[0]["timeout"] == 15.0

    # 2. Test custom state parameter
    await client.get_issues("om-3531/DevSquad", state="closed")
    assert calls[1]["params"] == {"state": "closed"}


@pytest.mark.asyncio
async def test_get_issues_pull_request_filtering(monkeypatch):
    """
    GitHub's GET /repos/{owner}/{repo}/issues returns both issues and pull requests.
    Pull requests include a 'pull_request' key in the response object.
    When filter_pull_requests=True, pull requests must be filtered out.
    """
    client = GitHubClient()

    mixed_payload = [
        {"id": 1, "number": 101, "title": "Pure Issue"},
        {"id": 2, "number": 102, "title": "PR disguised as issue", "pull_request": {"url": "https://api.github.com/repos/om-3531/DevSquad/pulls/102"}},
        {"id": 3, "number": 103, "title": "Another Genuine Issue"},
    ]

    async def mock_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(200, request=req, json=mixed_payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    # Filtered (default: filter_pull_requests=True)
    filtered = await client.get_issues("om-3531/DevSquad")
    assert len(filtered) == 2
    assert [item["number"] for item in filtered] == [101, 103]
    assert all("pull_request" not in item for item in filtered)

    # Unfiltered (filter_pull_requests=False)
    unfiltered = await client.get_issues("om-3531/DevSquad", filter_pull_requests=False)
    assert len(unfiltered) == 3
    assert [item["number"] for item in unfiltered] == [101, 102, 103]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 404, 429])
async def test_get_issues_http_errors(monkeypatch, status_code):
    client = GitHubClient()

    async def mock_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(status_code, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.get_issues("om-3531/DevSquad")
    assert exc_info.value.response.status_code == status_code


@pytest.mark.asyncio
async def test_get_repository_success_and_request_attributes(monkeypatch):
    client = GitHubClient()
    calls = []

    async def mock_get(self, url, **kwargs):
        calls.append({
            "url": str(url),
            "headers": kwargs.get("headers"),
            "timeout": self.timeout.read,
        })
        req = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=req,
            json={
                "id": 12345,
                "name": "DevSquad",
                "full_name": "om-3531/DevSquad",
                "default_branch": "main",
                "stargazers_count": 5,
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    repo_data = await client.get_repository("om-3531/DevSquad")
    assert repo_data["name"] == "DevSquad"
    assert repo_data["default_branch"] == "main"
    assert calls[0]["url"] == "https://api.github.com/repos/om-3531/DevSquad"
    assert calls[0]["headers"]["Accept"] == "application/vnd.github+json"
    assert calls[0]["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    assert calls[0]["headers"]["User-Agent"] == "DevSquad/0.1.0"
    assert calls[0]["timeout"] == 15.0


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 404, 429])
async def test_get_repository_http_errors(monkeypatch, status_code):
    client = GitHubClient()

    async def mock_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(status_code, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.get_repository("om-3531/DevSquad")
    assert exc_info.value.response.status_code == status_code


@pytest.mark.asyncio
async def test_get_tree_propagates_404_instead_of_returning_an_empty_tree(monkeypatch):
    async def mock_get(self, url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(404, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await RepositoryService().get_tree("owner/private-repository")

    assert exc_info.value.response.status_code == 404


# --- IssueService and Issue Creation Unit Tests ---

@pytest.mark.asyncio
async def test_issue_service_create_issue_success(monkeypatch):
    recorded_calls = []

    async def mock_post(self, url, **kwargs):
        recorded_calls.append({
            "url": str(url),
            "headers": kwargs.get("headers"),
            "json": kwargs.get("json"),
        })
        request = httpx.Request("POST", url)
        return httpx.Response(
            201,
            request=request,
            json={
                "id": 884422,
                "number": 12,
                "html_url": "https://github.com/om-3531/DevSquad/issues/12",
                "title": "Bug in query parser",
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    service = IssueService()
    result = await service.create_issue(
        repo="om-3531/DevSquad",
        title="Bug in query parser",
        body="Detailed bug explanation here.",
    )

    assert result == {
        "id": 884422,
        "html_url": "https://github.com/om-3531/DevSquad/issues/12",
    }
    assert len(recorded_calls) == 1
    assert recorded_calls[0]["url"] == "https://api.github.com/repos/om-3531/DevSquad/issues"
    assert recorded_calls[0]["json"] == {
        "title": "Bug in query parser",
        "body": "Detailed bug explanation here.",
    }


@pytest.mark.asyncio
async def test_issue_service_create_issue_without_body(monkeypatch):
    recorded_calls = []

    async def mock_post(self, url, **kwargs):
        recorded_calls.append(kwargs.get("json"))
        request = httpx.Request("POST", url)
        return httpx.Response(
            201,
            request=request,
            json={
                "id": 112233,
                "html_url": "https://github.com/om-3531/DevSquad/issues/13",
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    service = IssueService()
    result = await service.create_issue(
        repo="om-3531/DevSquad",
        title="Issue without body",
    )

    assert result["id"] == 112233
    assert result["html_url"] == "https://github.com/om-3531/DevSquad/issues/13"
    assert recorded_calls[0] == {"title": "Issue without body"}


@pytest.mark.asyncio
async def test_issue_service_create_issue_http_404(monkeypatch):
    async def mock_post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(404, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    service = IssueService()
    with pytest.raises(FileNotFoundError, match="GitHub repository 'om-3531/DevSquad' not found"):
        await service.create_issue(
            repo="om-3531/DevSquad",
            title="Non-existent repo issue",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_issue_service_create_issue_http_401_403(monkeypatch, status_code):
    async def mock_post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(status_code, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    service = IssueService()
    with pytest.raises(PermissionError, match="GitHub permission denied or unauthorized"):
        await service.create_issue(
            repo="om-3531/DevSquad",
            title="Permission denied issue",
        )


@pytest.mark.asyncio
async def test_issue_service_create_issue_http_429(monkeypatch):
    async def mock_post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(429, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    service = IssueService()
    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        await service.create_issue(
            repo="om-3531/DevSquad",
            title="Rate limit issue",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [500, 502, 503])
async def test_issue_service_create_issue_http_5xx(monkeypatch, status_code):
    async def mock_post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(status_code, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    service = IssueService()
    with pytest.raises(RuntimeError, match="GitHub server error"):
        await service.create_issue(
            repo="om-3531/DevSquad",
            title="Server error issue",
        )


@pytest.mark.asyncio
async def test_issue_service_create_issue_unexpected_status(monkeypatch):
    async def mock_post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json={"id": 1, "html_url": "url"})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    service = IssueService()
    with pytest.raises(RuntimeError, match="Unexpected GitHub API response status: HTTP 200"):
        await service.create_issue(
            repo="om-3531/DevSquad",
            title="Unexpected status issue",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_json",
    [
        {"html_url": "https://github.com/om-3531/DevSquad/issues/1"},  # missing id
        {"id": 12345},  # missing html_url
        {"id": "not_an_int", "html_url": "https://github.com/om-3531/DevSquad/issues/1"},  # non-int id
        {"id": 12345, "html_url": ""},  # empty html_url
        {"id": None, "html_url": None},  # null fields
    ],
)
async def test_issue_service_create_issue_malformed_success_response(monkeypatch, malformed_json):
    async def mock_post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(201, request=request, json=malformed_json)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    service = IssueService()
    with pytest.raises(ValueError, match="Malformed GitHub API response"):
        await service.create_issue(
            repo="om-3531/DevSquad",
            title="Malformed response test",
        )


@pytest.mark.asyncio
async def test_issue_service_create_issue_invalid_repository_and_empty_title():
    service = IssueService()

    # Invalid repository identifiers
    with pytest.raises(ValueError, match="Invalid GitHub repository identifier"):
        await service.create_issue(repo="invalid_repo_name", title="Valid Title")

    with pytest.raises(ValueError, match="Invalid GitHub repository identifier"):
        await service.create_issue(repo="owner/repo/extra", title="Valid Title")

    # Empty / whitespace titles
    with pytest.raises(ValueError, match="Issue title cannot be empty"):
        await service.create_issue(repo="om-3531/DevSquad", title="")

    with pytest.raises(ValueError, match="Issue title cannot be empty"):
        await service.create_issue(repo="om-3531/DevSquad", title="   ")


@pytest.mark.asyncio
async def test_issue_service_get_issue_unchanged(monkeypatch):
    async def mock_get(self, url, **kwargs):
        assert url == "https://api.github.com/repos/om-3531/DevSquad/issues/42"
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": 99999,
                "number": 42,
                "title": "Existing Issue",
                "state": "open",
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    service = IssueService()
    issue = await service.get_issue("om-3531/DevSquad", 42)
    assert issue["id"] == 99999
    assert issue["number"] == 42
    assert issue["title"] == "Existing Issue"
    assert issue["state"] == "open"


@pytest.mark.asyncio
async def test_github_client_create_issue(monkeypatch):
    client = GitHubClient()

    async def mock_post(self, url, **kwargs):
        assert url == "https://api.github.com/repos/om-3531/DevSquad/issues"
        assert kwargs["json"]["title"] == "Client issue"
        request = httpx.Request("POST", url)
        return httpx.Response(201, request=request, json={"id": 55, "number": 1, "html_url": "https://..."})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    data = await client.create_issue("om-3531/DevSquad", "Client issue", "Body text")
    assert data["id"] == 55

    # Respects GITHUB_WRITES_ENABLED=False
    monkeypatch.setattr(settings, "GITHUB_WRITES_ENABLED", False)
    with pytest.raises(GitHubWritesDisabledError):
        await client.create_issue("om-3531/DevSquad", "Client issue")

