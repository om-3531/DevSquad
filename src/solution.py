import json
import urllib.request
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional


class GitHubAPIError(Exception):
    """Exception raised for errors in the GitHub API."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"GitHub API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class GitHubClient:
    """Simple GitHub API client using urllib."""

    def __init__(self, base_url: str = "https://api.github.com") -> None:
        self.base_url = base_url.rstrip("/")

    def _request(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHubClient/1.0",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except urllib.error.HTTPError as e:
            try:
                error_body = e.read().decode("utf-8")
                error_json = json.loads(error_body)
                message = error_json.get("message", e.reason)
            except Exception:
                message = e.reason
            raise GitHubAPIError(e.code, message) from None

    def get_repo(self, owner: str, repo: str) -> Dict[str, Any]:
        """Get repository information."""
        return self._request(f"/repos/{owner}/{repo}")

    def list_issues(self, owner: str, repo: str, state: str = "open") -> List[Dict[str, Any]]:
        """List issues for a repository."""
        return self._request(f"/repos/{owner}/{repo}/issues", {"state": state})

    def get_issue(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        """Get a single issue."""
        return self._request(f"/repos/{owner}/{repo}/issues/{issue_number}")


# ----------------- Tests -----------------

def test_get_repo_success() -> None:
    client = GitHubClient()
    repo = client.get_repo("octocat", "Hello-World")
    assert repo["full_name"] == "octocat/Hello-World"
    assert "description" in repo
    assert isinstance(repo["stargazers_count"], int)


def test_list_issues_success() -> None:
    client = GitHubClient()
    issues = client.list_issues("octocat", "Hello-World")
    assert isinstance(issues, list)
    if issues:
        issue = issues[0]
        assert "id" in issue
        assert "title" in issue
        assert "state" in issue
        assert issue["state"] in ("open", "closed")


def test_get_repo_not_found() -> None:
    client = GitHubClient()
    try:
        client.get_repo("nonexistent-owner-xyz", "nonexistent-repo-xyz")
        assert False, "Expected GitHubAPIError for 404"
    except GitHubAPIError as e:
        assert e.status_code == 404


def test_get_issue_success() -> None:
    client = GitHubClient()
    issue = client.get_issue("octocat", "Hello-World", 348)
    assert issue["number"] == 348
    assert "title" in issue
    assert "state" in issue


def test_get_issue_not_found() -> None:
    client = GitHubClient()
    try:
        client.get_issue("octocat", "Hello-World", 999999)
        assert False, "Expected GitHubAPIError for 404"
    except GitHubAPIError as e:
        assert e.status_code == 404
        assert "Not Found" in e.message or "not found" in e.message.lower()