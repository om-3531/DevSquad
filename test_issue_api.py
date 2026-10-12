
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_github_issue_success():
    mock_issue = {
        "title": "Sample issue",
        "body": "Sample description",
        "state": "open",
        "number": 1,
    }

    with patch.object(
        app,
        "unused_test_attribute",
        create=True,
    ):
        pass

    with patch(
        "backend.main.issue_service.get_issue",
        new_callable=AsyncMock,
    ) as mock_get_issue:
        mock_get_issue.return_value = mock_issue

        response = client.post(
            "/github/issue",
            json={
                "repo": "om-3531/DevSquad",
                "issue_number": 1,
            },
        )

    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert data["issue"]["title"] == "Sample issue"
    assert data["issue"]["state"] == "open"


def test_github_issue_missing_repo():
    response = client.post(
        "/github/issue",
        json={"issue_number": 1},
    )

    assert response.status_code == 422
