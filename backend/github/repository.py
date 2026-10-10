
import base64
import httpx

from backend.github.client import GitHubClient


class RepositoryService:
    def __init__(self):
        self.github = GitHubClient()

    async def get_file(
        self,
        repo: str,
        path: str,
        branch: str = "main",
    ):
        url = (
            f"{self.github.base_url}/repos/{repo}/contents/{path}"
            f"?ref={branch}"
        )

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=self.github.headers,
            )

        response.raise_for_status()
        data = response.json()

        if data.get("encoding") == "base64" and data.get("content"):
            decoded_content = base64.b64decode(
                data["content"]
            ).decode("utf-8")

            data["decoded_content"] = decoded_content
            data.pop("content", None)

        return data

    async def get_repository(self, repo: str):
        url = f"{self.github.base_url}/repos/{repo}"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=self.github.headers,
            )

        response.raise_for_status()
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
