import os
import httpx
from typing import Any, Dict, List, Optional


class GitHubClient:
    """
    Simple GitHub API client for integration testing.
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("GitHub token must be provided")
        self.token = token
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }

    def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{endpoint}"
        response = httpx.request(method, url, headers=self.headers, params=params, timeout=10.0)
        if response.status_code != 200:
            raise RuntimeError(f"GitHub API request failed: {response.status_code} {response.text}")
        return response.json()

    def get_user(self) -> Dict[str, Any]:
        """
        Retrieve authenticated user information.
        """
        return self._request("GET", "/user")

    def get_repos(self, visibility: str = "all") -> List[Dict[str, Any]]:
        """
        Retrieve repositories for the authenticated user.
        """
        return self._request("GET", "/user/repos", params={"visibility": visibility})

    def get_rate_limit(self) -> Dict[str, Any]:
        """
        Retrieve current rate limit status.
        """
        return self._request("GET", "/rate_limit")


def run_integration_test() -> bool:
    """
    Perform a basic integration test against the GitHub API.
    Returns True if all checks pass, otherwise raises an exception.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("Environment variable GITHUB_TOKEN is not set")

    client = GitHubClient(token)

    # Test user endpoint
    user = client.get_user()
    if "login" not in user:
        raise RuntimeError("User response missing 'login' field")

    # Test repos endpoint
    repos = client.get_repos()
    if not isinstance(repos, list):
        raise RuntimeError("Repos response is not a list")

    # Test rate limit endpoint
    rate_limit = client.get_rate_limit()
    if "resources" not in rate_limit:
        raise RuntimeError("Rate limit response missing 'resources' field")

    return True


if __name__ == "__main__":
    try:
        success = run_integration_test()
        print(f"Integration test passed: {success}")
    except Exception as exc:
        print(f"Integration test failed: {exc}")
        raise