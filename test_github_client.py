
import httpx
import pytest

from backend.github.client import GitHubClient


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
        return "abc123"

    async def mock_post(self, url, **kwargs):
        assert url.endswith("/git/refs")
        assert kwargs["json"] == {
            "ref": "refs/heads/devsquad/test-branch",
            "sha": "abc123",
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
    )

    assert result["ref"] == "refs/heads/devsquad/test-branch"


@pytest.mark.asyncio
async def test_create_pull_request(monkeypatch):
    async def mock_post(self, url, **kwargs):
        assert url.endswith("/pulls")
        assert kwargs["json"]["head"] == "devsquad/test-branch"
        assert kwargs["json"]["base"] == "main"

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

    client = GitHubClient()
    result = await client.create_pull_request(
        repo="om-3531/DevSquad",
        title="Test pull request",
        head="devsquad/test-branch",
        body="Automated test",
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
        return "old-commit-sha"

    responses = [
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
    )

    assert result["commit_sha"] == "new-commit-sha"
    assert result["branch"] == "devsquad/test-branch"
    assert result["files"] == ["example.py"]

