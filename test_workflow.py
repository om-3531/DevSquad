
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def setup_workflow_mocks(review, test_result):
    issue = {
        "title": "Add even-odd function",
        "body": "Create a function that checks whether a number is even or odd.",
        "number": 1,
    }

    patches = [
        patch(
            "backend.main.issue_service.get_issue",
            new_callable=AsyncMock,
            return_value=issue,
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
            return_value=(
                "def check_even_odd(number):\n"
                "    return 'Even' if number % 2 == 0 else 'Odd'\n"
            ),
        ),
        patch(
            "backend.main.tester_agent.generate_tests",
            return_value=(
                "from candidate import check_even_odd\n"
                "def test_even():\n"
                "    assert check_even_odd(2) == 'Even'\n"
            ),
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

    return patches


def test_workflow_review_requests_changes():
    patches = setup_workflow_mocks(
        review="VERDICT: REQUEST_CHANGES\nPlease improve the code.",
        test_result={
            "status": "NOT_RUN",
            "passed": False,
            "summary": "Tests were not executed.",
            "return_code": None,
        },
    )

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        with patch(
            "backend.main.coder_agent.improve_code",
            return_value="def check_even_odd(number):\n    return 'Even'\n",
        ):
            response = client.post(
                "/agents/workflow",
                json={"repo": "om-3531/DevSquad", "issue_number": 1},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["quality_gate"]["status"] == "REQUEST_CHANGES"
    assert data["repository_modified"] is False


def test_workflow_generated_tests_fail():
    patches = setup_workflow_mocks(
        review="VERDICT: APPROVE",
        test_result={
            "status": "TESTS_FAILED",
            "passed": False,
            "summary": "1 test failed.",
            "return_code": 1,
        },
    )

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        response = client.post(
            "/agents/workflow",
            json={"repo": "om-3531/DevSquad", "issue_number": 1},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["quality_gate"]["status"] == "GENERATED_TESTS_FAILED"
    assert data["repository_modified"] is False
