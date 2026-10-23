import os
import httpx
import pytest

class GitHubClient:
    """
    Simple wrapper around the GitHub REST API.
    """
    def __init__(self, token: str | None = None, base_url: str = "https://api.github.com"):
        self.base_url = base_url.rstrip("/")
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.headers = {"Accept": "application/vnd.github.v3+json"}
        if self.token:
            self.headers["Authorization"] = f"token {self.token}"

    def _request(self, method: str, url: str, **kwargs):
        full_url = f"{self.base_url}{url}"
        response = httpx.request(method, full_url, headers=self.headers, **kwargs)
        response.raise_for_status()
        return response.json()

    def get_repo_issues(self, owner: str, repo: str, state: str = "open"):
        """
        Retrieve issues for a repository.
        """
        return self._request("GET", f"/repos/{owner}/{repo}/issues", params={"state": state})

    def create_issue(self, owner: str, repo: str, title: str, body: str | None = None):
        """
        Create a new issue in the specified repository.
        """
        data = {"title": title}
        if body:
            data["body"] = body
        return self._request("POST", f"/repos/{owner}/{repo}/issues", json=data)

    def close_issue(self, owner: str, repo: str, issue_number: int):
        """
        Close an existing issue.
        """
        data = {"state": "closed"}
        return self._request("PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", json=data)


def test_get_public_repo_issues():
    """
    Test retrieving issues from a public repository.
    """
    client = GitHubClient()
    issues = client.get_repo_issues("octocat", "Hello-World")
    assert isinstance(issues, list)
    if issues:
        issue = issues[0]
        assert "id" in issue
        assert "title" in issue


def test_create_and_close_issue():
    """
    Test creating and closing an issue in a repository owned by the authenticated user.
    Requires GITHUB_TOKEN and GITHUB_REPO environment variables.
    """
    token = os.getenv("GITHUB_TOKEN")
    repo_full = os.getenv("GITHUB_REPO")
    if not token or not repo_full:
        pytest.skip("GITHUB_TOKEN or GITHUB_REPO not set; skipping authenticated tests")

    owner, repo = repo_full.split("/")
    client = GitHubClient(token=token)

    title = "DevSquad integration test issue"
    created = client.create_issue(owner, repo, title, body="Automated test issue")
    assert created["title"] == title
    issue_number = created["number"]

    closed = client.close_issue(owner, repo, issue_number)
    assert closed["state"] == "closed"