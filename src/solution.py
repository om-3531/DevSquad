import os
import json
import base64
import requests
from typing import Dict, Any

GITHUB_API_URL = "https://api.github.com"
DEFAULT_TIMEOUT = 10  # seconds


class GitHubClient:
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{GITHUB_API_URL}{endpoint}"
        try:
            resp = requests.request(method, url, headers=self.headers, timeout=DEFAULT_TIMEOUT, **kwargs)
        except requests.RequestException as e:
            raise RuntimeError(f"Network error: {e}") from e

        if resp.status_code == 404:
            raise FileNotFoundError(f"Endpoint not found: {url}")
        if resp.status_code in (401, 403):
            raise PermissionError(f"Authentication failed for {url}")
        if resp.status_code == 429:
            raise RuntimeError(f"Rate limit exceeded for {url}")
        if resp.status_code >= 500:
            raise RuntimeError(f"Server error {resp.status_code} for {url}")

        return resp

    def get_issue(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        endpoint = f"/repos/{owner}/{repo}/issues/{issue_number}"
        resp = self._request("GET", endpoint)
        return resp.json()

    def create_branch(self, owner: str, repo: str, new_branch: str, base_sha: str) -> None:
        endpoint = f"/repos/{owner}/{repo}/git/refs"
        data = {"ref": f"refs/heads/{new_branch}", "sha": base_sha}
        resp = self._request("POST", endpoint, json=data)
        if resp.status_code != 201:
            raise RuntimeError(f"Failed to create branch {new_branch}: {resp.text}")

    def commit_file(self, owner: str, repo: str, branch: str, file_path: str, content: str, message: str) -> None:
        endpoint = f"/repos/{owner}/{repo}/contents/{file_path}"
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        data = {"message": message, "content": encoded_content, "branch": branch}
        resp = self._request("PUT", endpoint, json=data)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to commit file {file_path}: {resp.text}")

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str,
        draft: bool = True,
    ) -> Dict[str, Any]:
        endpoint = f"/repos/{owner}/{repo}/pulls"
        data = {"title": title, "head": head, "base": base, "body": body, "draft": draft}
        resp = self._request("POST", endpoint, json=data)
        if resp.status_code != 201:
            raise RuntimeError(f"Failed to create PR: {resp.text}")
        return resp.json()


def main() -> None:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise EnvironmentError("GITHUB_TOKEN environment variable not set")

    owner = os.getenv("GITHUB_OWNER", "devsquad")
    repo = os.getenv("GITHUB_REPO", "devsquad")
    issue_number = int(os.getenv("ISSUE_NUMBER", "1"))

    client = GitHubClient(token)

    issue = client.get_issue(owner, repo, issue_number)
    print(f"Issue #{issue_number} title: {issue.get('title')}")

    base_branch = "main"
    base_ref_endpoint = f"/repos/{owner}/{repo}/git/refs/heads/{base_branch}"
    base_ref_resp = client._request("GET", base_ref_endpoint)
    base_sha = base_ref_resp.json()["object"]["sha"]

    draft_branch = f"issue-{issue_number}-draft"
    client.create_branch(owner, repo, draft_branch, base_sha)

    placeholder_content = "# Placeholder\n\nThis is a draft branch for issue integration."
    client.commit_file(owner, repo, draft_branch, "README.md", placeholder_content, "Add placeholder README")

    pr_title = f"Draft PR for issue #{issue_number}"
    pr_body = f"Automated draft PR for issue #{issue_number}."
    pr = client.create_pull_request(owner, repo, pr_title, draft_branch, base_branch, pr_body, draft=True)
    print(f"Draft PR created: {pr.get('html_url')}")


if __name__ == "__main__":
    main()