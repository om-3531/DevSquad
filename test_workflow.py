
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

REPO = "om-3531/DevSquad"
CODE = (
    "def check_even_odd(number):\n"
    "    return 'Even' if number % 2 == 0 else 'Odd'\n"
)
TESTS = (
    "from candidate import check_even_odd\n"
    "def test_even():\n"
    "    assert check_even_odd(2) == 'Even'\n"
)
PASSED = {
    "status": "TESTS_PASSED",
    "passed": True,
    "summary": "1 passed",
    "return_code": 0,
}
FAILED = {
    "status": "TESTS_FAILED",
    "passed": False,
    "summary": "1 test failed",
    "return_code": 1,
}


def setup_workflow_mocks(review, test_result):
    issue = {
        "title": "Add even-odd function",
        "body": "Create a function that checks whether a number is even or odd.",
        "number": 1,
    }

    return [
        patch(
            "backend.main.issue_service.get_issue",
            new_callable=AsyncMock,
            return_value=issue,
        ),
        patch(
            "backend.main.repository_service.get_repository",
            new_callable=AsyncMock,
            return_value={"default_branch": "main"},
        ),
        patch(
            "backend.main.repository_service.get_file",
            new_callable=AsyncMock,
            return_value={"decoded_content": "# Sample repository"},
        ),
        patch(
            "backend.main.planner_agent.create_plan",
            return_value={"steps": ["Implement function"]},
        ),
        patch(
            "backend.main.coder_agent.generate_code",
            return_value=CODE,
        ),
        patch(
            "backend.main.tester_agent.generate_tests",
            return_value=TESTS,
        ),
        patch(
            "backend.main.reviewer_agent.review_code",
            return_value=review,
        ),
        patch(
            "backend.main.generated_test_runner.run_generated_tests",
            return_value=test_result,
        ),
    ]


def activate_patches(patches):
    for item in patches:
        item.start()


def stop_patches(patches):
    for item in reversed(patches):
        item.stop()


def test_workflow_review_requests_changes():
    patches = setup_workflow_mocks(
        "VERDICT: REQUEST_CHANGES\nPlease improve the code.",
        FAILED,
    )
    activate_patches(patches)

    try:
        with patch(
            "backend.main.coder_agent.improve_code",
            return_value="def check_even_odd(number):\n    return 'Even'\n",
        ):
            response = client.post(
                "/agents/workflow",
                json={"repo": REPO, "issue_number": 1},
            )
    finally:
        stop_patches(patches)

    assert response.status_code == 200
    assert response.json()["quality_gate"]["status"] == "REQUEST_CHANGES"
    assert response.json()["repository_modified"] is False


def test_workflow_generated_tests_fail():
    patches = setup_workflow_mocks("VERDICT: APPROVE", FAILED)
    activate_patches(patches)

    try:
        with (
            patch(
                "backend.main.coder_agent.improve_code",
                return_value=CODE,
            ) as improve,
            patch("backend.main.github_client.create_branch") as branch,
            patch("backend.main.github_client.commit_files") as commit,
            patch("backend.main.github_client.create_pull_request") as pr,
        ):
            response = client.post(
                "/agents/workflow",
                json={
                    "repo": REPO,
                    "issue_number": 1,
                    "target_path": "src/check_even_odd.py",
                },
            )
    finally:
        stop_patches(patches)

    assert response.status_code == 200
    assert response.json()["quality_gate"]["status"] == "GENERATED_TESTS_FAILED"
    assert response.json()["repository_modified"] is False
    assert response.json()["correction_attempts"] == 2
    improve.assert_called()
    branch.assert_not_called()
    commit.assert_not_called()
    pr.assert_not_called()


def test_workflow_passes_without_target_path():
    patches = setup_workflow_mocks("VERDICT: APPROVE", PASSED)
    activate_patches(patches)

    try:
        with (
            patch("backend.main.github_client.create_branch") as branch,
            patch("backend.main.github_client.commit_files") as commit,
            patch("backend.main.github_client.create_pull_request") as pr,
        ):
            response = client.post(
                "/agents/workflow",
                json={"repo": REPO, "issue_number": 1},
            )
    finally:
        stop_patches(patches)

    assert response.status_code == 200
    assert response.json()["quality_gate"]["approved"] is True
    assert response.json()["repository_modified"] is False
    branch.assert_not_called()
    commit.assert_not_called()
    pr.assert_not_called()


def test_workflow_creates_draft_pr_for_new_file():
    patches = setup_workflow_mocks("VERDICT: APPROVE", PASSED)
    activate_patches(patches)

    try:
        with (
            patch(
                "backend.main.repository_service.get_file",
                new_callable=AsyncMock,
                side_effect=[
                    {"decoded_content": "# Sample repository"},
                    httpx.HTTPStatusError(
                        "Not found",
                        request=httpx.Request(
                            "GET", "https://api.github.com/test"
                        ),
                        response=httpx.Response(
                            404,
                            request=httpx.Request(
                                "GET", "https://api.github.com/test"
                            ),
                        ),
                    ),
                ],
            ),
            patch(
                "backend.main.github_client.create_branch",
                new_callable=AsyncMock,
                return_value={"ref": "refs/heads/devsquad/test"},
            ) as branch,
            patch(
                "backend.main.github_client.commit_files",
                new_callable=AsyncMock,
                return_value={
                    "branch": "devsquad/test",
                    "commit_sha": "abc123",
                    "commit_url": "https://github.com/test/commit/abc123",
                    "files": ["src/check_even_odd.py"],
                },
            ) as commit,
            patch(
                "backend.main.github_client.create_pull_request",
                new_callable=AsyncMock,
                return_value={
                    "number": 42,
                    "html_url": "https://github.com/test/pull/42",
                    "draft": True,
                },
            ) as pr,
        ):
            response = client.post(
                "/agents/workflow",
                json={
                    "repo": REPO,
                    "issue_number": 1,
                    "target_path": "src/check_even_odd.py",
                },
            )
    finally:
        stop_patches(patches)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["repository_modified"] is True
    assert data["pull_request"]["number"] == 42
    assert data["branch"]
    branch.assert_awaited_once()
    commit.assert_awaited_once()
    pr.assert_awaited_once()
    assert pr.await_args.kwargs["draft"] is True
    assert "merge" not in pr.await_args.kwargs


def test_workflow_rejects_existing_target_file():
    patches = setup_workflow_mocks("VERDICT: APPROVE", PASSED)
    activate_patches(patches)

    try:
        with (
            patch(
                "backend.main.repository_service.get_file",
                new_callable=AsyncMock,
                side_effect=[
                    {"decoded_content": "# Sample repository"},
                    {"decoded_content": "# Existing file"},
                ],
            ),
            patch("backend.main.github_client.create_branch") as branch,
        ):
            response = client.post(
                "/agents/workflow",
                json={
                    "repo": REPO,
                    "issue_number": 1,
                    "target_path": "src/check_even_odd.py",
                },
            )
    finally:
        stop_patches(patches)

    assert response.status_code == 409
    branch.assert_not_called()

def test_workflow_invalid_verdict_does_not_create_pr():
    patches = setup_workflow_mocks("Review inconclusive", PASSED)
    activate_patches(patches)

    try:
        with (
            patch(
                "backend.main.github_client.create_branch",
                new_callable=AsyncMock,
            ) as branch,
            patch(
                "backend.main.github_client.commit_files",
                new_callable=AsyncMock,
            ) as commit,
            patch(
                "backend.main.github_client.create_pull_request",
                new_callable=AsyncMock,
            ) as pr,
        ):
            response = client.post(
                "/agents/workflow",
                json={
                    "repo": REPO,
                    "issue_number": 1,
                    "target_path": "src/check_even_odd.py",
                },
            )
    finally:
        stop_patches(patches)

    assert response.status_code == 200
    assert (
        response.json()["quality_gate"]["status"]
        == "NEEDS_MANUAL_REVIEW"
    )
    assert response.json()["repository_modified"] is False
    branch.assert_not_called()
    commit.assert_not_called()
    pr.assert_not_called()
