
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
            self.headers["Authorization"] = (
                f"Bearer {settings.GITHUB_TOKEN}"
            )

    async def get_issue(self, repo: str, issue_number: int):
        url = f"{self.base_url}/repos/{repo}/issues/{issue_number}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def get_branch_sha(self, repo: str, branch: str = "main"):
        url = f"{self.base_url}/repos/{repo}/git/ref/heads/{branch}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()["object"]["sha"]

    async def create_branch(
        self,
        repo: str,
        branch_name: str,
        source_branch: str = "main",
    ):
        if not branch_name or branch_name.startswith("/"):
            raise ValueError("A valid branch name is required.")

        if branch_name == source_branch:
            raise ValueError("The new branch must differ from the source branch.")

        source_sha = await self.get_branch_sha(repo, source_branch)
        url = f"{self.base_url}/repos/{repo}/git/refs"

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

    async def commit_files(
        self,
        repo: str,
        branch: str,
        files: dict[str, str],
        message: str,
    ):
        if not files:
            raise ValueError("At least one file is required.")
        if not branch or branch == "main":
            raise ValueError("Changes must target a feature branch.")
        if not message.strip():
            raise ValueError("Commit message cannot be empty.")

        for path, content in files.items():
            if (
                not path
                or path.startswith("/")
                or "\\" in path
                or ".." in path.split("/")
                or not isinstance(content, str)
            ):
                raise ValueError(f"Invalid file path or content: {path!r}")

        branch_sha = await self.get_branch_sha(repo, branch)
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

            response = await client.patch(
                f"{self.base_url}/repos/{repo}/git/refs/heads/{branch}",
                headers=self.headers,
                json={"sha": new_commit["sha"], "force": False},
            )
            response.raise_for_status()

            return {
                "commit_sha": new_commit["sha"],
                "commit_url": new_commit["html_url"],
                "branch": branch,
                "files": list(files.keys()),
            }

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
        draft: bool = True,
    ):
        if not title.strip():
            raise ValueError("Pull request title cannot be empty.")
        if not head or not base or head == base:
            raise ValueError("Valid, different head and base branches are required.")

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
