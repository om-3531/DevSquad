
from unittest.mock import AsyncMock, patch

import httpx
import pytest
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
            "backend.main.repository_service.get_tree",
            new_callable=AsyncMock,
            return_value=["README.md", "src/check_even_odd.py"],
        ),
        patch(
            "backend.main.repository_service.get_branch_sha",
            new_callable=AsyncMock,
            return_value="a" * 40,
        ),
        patch(
            "backend.main.repository_service.get_file",
            new_callable=AsyncMock,
            return_value={"decoded_content": "# Sample repository", "sha": "b" * 40},
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
        (
            patch(
                "backend.main.reviewer_agent.review_code",
                side_effect=review,
            )
            if isinstance(review, (list, tuple))
            else patch(
                "backend.main.reviewer_agent.review_code",
                return_value=review,
            )
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
    assert response.json()["test_execution"]["status"] == "BLOCKED_BY_REVIEW"
    assert response.json()["testing_status"] == "BLOCKED_BY_REVIEW"
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


def test_workflow_simulates_writes_for_a_verified_new_file():
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
    assert data["repository_modified"] is False
    assert data["pull_request"] is None
    assert data["branch"] is None
    assert data["write_simulation"]["mode"] == "dry_run"
    assert data["write_simulation"]["performed"] is False
    assert data["diff_preview"]["path"] == "src/check_even_odd.py"
    assert data["diff_preview"]["operation"] == "create"
    assert data["diff_preview"]["original_content_available"] is True
    assert data["diff_preview"]["reliable"] is True
    assert "--- /dev/null" in data["diff_preview"]["diff"]
    assert data["diff_previews"] == [data["diff_preview"]]
    assert data["proposal_id"] == data["proposal"]["proposal_id"]
    assert data["proposal"]["status"] == "PENDING_REVIEW"
    assert data["proposal"]["target_branch"] == "main"
    branch.assert_not_called()
    commit.assert_not_called()
    pr.assert_not_called()


def test_workflow_modifies_existing_target_file():
    patches = setup_workflow_mocks("VERDICT: APPROVE", PASSED)
    activate_patches(patches)

    try:
        with (
            patch(
                "backend.main.repository_service.get_file",
                new_callable=AsyncMock,
                side_effect=[
                    {"decoded_content": "# Sample repository"},
                    {"decoded_content": "def existing_function():\n    pass\n"},
                ],
            ),
            patch(
                "backend.main.coder_agent.generate_code",
                return_value="def existing_function():\n    pass\n\ndef check_even_odd(number):\n    return 'Even' if number % 2 == 0 else 'Odd'\n",
            ) as generate,
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
    assert data["repository_modified"] is False
    assert data["operation"] == "modify"
    assert data["files_changed"] == [{"path": "src/check_even_odd.py", "operation": "modify"}]
    assert generate.call_args.kwargs["operation"] == "modify"
    assert "existing_function" in generate.call_args.kwargs["existing_code"]
    assert data["write_simulation"]["mode"] == "dry_run"
    assert data["diff_preview"]["operation"] == "modify"
    assert "     pass" in data["diff_preview"]["diff"]
    assert "+    return 'Even'" in data["diff_preview"]["diff"]
    branch.assert_not_called()
    commit.assert_not_called()
    pr.assert_not_called()


def test_workflow_rejects_unsafe_target_path():
    response = client.post(
        "/agents/workflow",
        json={
            "repo": REPO,
            "issue_number": 1,
            "target_path": "../../etc/passwd.py",
        },
    )
    assert response.status_code == 422


def test_workflow_reports_an_unreliable_diff_when_original_text_is_unavailable():
    patches = setup_workflow_mocks("VERDICT: APPROVE", PASSED)
    activate_patches(patches)

    try:
        with patch(
            "backend.main.repository_service.get_file",
            new_callable=AsyncMock,
            return_value={"encoding": "base64"},
        ):
            response = client.post(
                "/agents/workflow",
                json={"repo": REPO, "issue_number": 1, "target_path": "src/check_even_odd.py"},
            )
    finally:
        stop_patches(patches)

    assert response.status_code == 200
    preview = response.json()["diff_preview"]
    assert preview["original_content_available"] is False
    assert preview["reliable"] is False
    assert preview["diff"] == ""
    assert preview["changed"] is False


def test_workflow_dry_run_does_not_attempt_a_commit():
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
                        request=httpx.Request("GET", "https://api.github.com/test"),
                        response=httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/test")),
                    ),
                ],
            ),
            patch(
                "backend.main.github_client.create_branch",
                new_callable=AsyncMock,
                return_value={"ref": "refs/heads/devsquad/test"},
            ),
            patch(
                "backend.main.github_client.commit_files",
                new_callable=AsyncMock,
                side_effect=httpx.HTTPStatusError(
                    "Conflict",
                    request=httpx.Request("POST", "https://api.github.com/test"),
                    response=httpx.Response(409, request=httpx.Request("POST", "https://api.github.com/test")),
                ),
            ),
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
    assert response.json()["repository_modified"] is False
    assert response.json()["write_simulation"]["performed"] is False


def test_workflow_planner_identifies_target_file_and_criteria():
    patches = setup_workflow_mocks("VERDICT: APPROVE", PASSED)
    activate_patches(patches)

    try:
        with (
            patch(
                "backend.main.planner_agent.create_plan",
                return_value={
                    "steps": ["Update function"],
                    "acceptance_criteria": ["Check positive and negative numbers"],
                    "target_files": [{"path": "src/check_even_odd.py", "operation": "modify"}],
                },
            ),
            patch(
                "backend.main.repository_service.get_file",
                new_callable=AsyncMock,
                side_effect=[
                    {"decoded_content": "# Sample repository"},
                    {"decoded_content": "def check_even_odd(): pass"},
                ],
            ),
            patch(
                "backend.main.github_client.create_branch",
                new_callable=AsyncMock,
                return_value={"ref": "refs/heads/devsquad/test"},
            ),
            patch(
                "backend.main.github_client.commit_files",
                new_callable=AsyncMock,
                return_value={
                    "branch": "devsquad/test",
                    "commit_sha": "def456",
                    "commit_url": "https://github.com/test/commit/def456",
                    "files": ["src/check_even_odd.py"],
                },
            ),
            patch(
                "backend.main.github_client.create_pull_request",
                new_callable=AsyncMock,
                return_value={
                    "number": 99,
                    "html_url": "https://github.com/test/pull/99",
                    "draft": True,
                },
            ) as pr,
        ):
            response = client.post(
                "/agents/workflow",
                json={
                    "repo": REPO,
                    "issue_number": 1,
                },
            )
    finally:
        stop_patches(patches)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["target_path"] == "src/check_even_odd.py"
    assert data["operation"] == "modify"
    assert data["repository_modified"] is False
    assert "Check positive and negative numbers" in data["acceptance_criteria"]
    assert data["write_simulation"]["mode"] == "dry_run"
    pr.assert_not_called()

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


def test_workflow_docker_error():
    docker_err = {
        "status": "DOCKER_ERROR",
        "passed": False,
        "summary": "Docker daemon is not running.",
        "return_code": None,
    }
    patches = setup_workflow_mocks("VERDICT: APPROVE", docker_err)
    activate_patches(patches)

    try:
        with (
            patch("backend.main.coder_agent.improve_code") as improve,
            patch("backend.main.github_client.create_branch") as branch,
        ):
            response = client.post(
                "/agents/workflow",
                json={"repo": REPO, "issue_number": 1, "target_path": "src/test.py"},
            )
    finally:
        stop_patches(patches)

    assert response.status_code == 200
    data = response.json()
    assert data["quality_gate"]["status"] == "TEST_ENVIRONMENT_ERROR"
    assert data["quality_gate"]["approved"] is False
    assert data["repository_modified"] is False
    assert data["correction_attempts"] == 0
    improve.assert_not_called()
    branch.assert_not_called()


def test_workflow_coder_failure():
    issue = {"title": "Issue", "body": "Body", "number": 1}
    with (
        patch("backend.main.issue_service.get_issue", new_callable=AsyncMock, return_value=issue),
        patch("backend.main.repository_service.get_repository", new_callable=AsyncMock, return_value={"default_branch": "main"}),
        patch("backend.main.repository_service.get_branch_sha", new_callable=AsyncMock, return_value="a" * 40),
            patch("backend.main.repository_service.get_tree", new_callable=AsyncMock, return_value=[]),
        patch("backend.main.repository_service.get_file", new_callable=AsyncMock, return_value={"decoded_content": ""}),
        patch("backend.main.planner_agent.create_plan", return_value={"steps": []}),
        patch("backend.main.coder_agent.generate_code", side_effect=ValueError("Code generation failed: Coder output was truncated.")),
    ):
        response = client.post(
            "/agents/workflow",
            json={"repo": REPO, "issue_number": 1},
        )

    assert response.status_code == 500
    assert "truncated" in response.json()["detail"]


def test_workflow_rate_limit_returns_429():
    from groq import RateLimitError
    req = httpx.Request("POST", "https://api.groq.com")
    resp = httpx.Response(429, request=req)
    rate_limit_err = RateLimitError("Rate limit reached for model openai/gpt-oss-20b", response=resp, body=None)

    issue = {"title": "Issue", "body": "Body", "number": 1}
    with (
        patch("backend.main.issue_service.get_issue", new_callable=AsyncMock, return_value=issue),
        patch("backend.main.repository_service.get_repository", new_callable=AsyncMock, return_value={"default_branch": "main"}),
        patch("backend.main.repository_service.get_branch_sha", new_callable=AsyncMock, return_value="a" * 40),
            patch("backend.main.repository_service.get_tree", new_callable=AsyncMock, return_value=[]),
        patch("backend.main.repository_service.get_file", new_callable=AsyncMock, return_value={"decoded_content": ""}),
        patch("backend.main.planner_agent.create_plan", return_value={"steps": []}),
        patch("backend.main.coder_agent.generate_code", return_value="def solution(): pass"),
        patch("backend.main.reviewer_agent.review_code", return_value="VERDICT: APPROVE"),
        patch("backend.main.tester_agent.generate_tests", side_effect=rate_limit_err),
    ):
        response = client.post(
            "/agents/workflow",
            json={"repo": REPO, "issue_number": 1},
        )

    assert response.status_code == 429
    assert "rate limit exceeded" in response.json()["detail"].lower()


def test_workflow_correction_followed_by_approval():
    patches = setup_workflow_mocks(
        [
            "VERDICT: REQUEST_CHANGES\nIssues: Fix syntax.\nFixes: Return valid code.",
            "VERDICT: APPROVE\nIssues: None\nFixes: None",
        ],
        PASSED,
    )
    activate_patches(patches)

    try:
        with (
            patch(
                "backend.main.coder_agent.improve_code",
                return_value="def check_even_odd(number):\n    return 'Even' if number % 2 == 0 else 'Odd'\n",
            ) as improve_mock,
        ):
            response = client.post(
                "/agents/workflow",
                json={"repo": REPO, "issue_number": 1},
            )
    finally:
        stop_patches(patches)

    assert response.status_code == 200
    data = response.json()
    assert data["quality_gate"]["status"] == "REVIEW_APPROVED_GENERATED_TESTS_PASSED"
    assert data["quality_gate"]["approved"] is True
    assert data["correction_attempts"] == 1
    assert data["test_execution"]["status"] == "PASSED"
    improve_mock.assert_called_once()


@pytest.mark.parametrize("status_code", [401, 403, 500])
def test_workflow_github_auth_and_server_errors(status_code):
    req = httpx.Request("GET", f"https://api.github.com/repos/{REPO}/issues/1")
    resp = httpx.Response(status_code, request=req)
    http_err = httpx.HTTPStatusError("GitHub error", request=req, response=resp)

    with patch(
        "backend.main.issue_service.get_issue",
        new_callable=AsyncMock,
        side_effect=http_err,
    ):
        response = client.post(
            "/agents/workflow",
            json={"repo": REPO, "issue_number": 1},
        )

    assert response.status_code == status_code

