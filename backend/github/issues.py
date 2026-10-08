from backend.github.client import GitHubClient


class IssueService:
    def __init__(self):
        self.github = GitHubClient()

    async def get_issue(self, repo: str, issue_number: int):
        return await self.github.get_issue(
            repo=repo,
            issue_number=issue_number,
        )
