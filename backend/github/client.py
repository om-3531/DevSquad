import httpx

from backend.config import settings


class GitHubClient:
    def __init__(self):
        self.base_url = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        if settings.GITHUB_TOKEN:
            self.headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"

    async def get_issue(self, repo: str, issue_number: int):
        url = f"{self.base_url}/repos/{repo}/issues/{issue_number}"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=self.headers,
            )

        response.raise_for_status()
        return response.json()
