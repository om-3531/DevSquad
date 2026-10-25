
from unittest.mock import AsyncMock, patch

import httpx
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


def test_github_issue_not_found():
    with patch(
        "backend.main.issue_service.get_issue",
        new_callable=AsyncMock,
        side_effect=httpx.HTTPStatusError(
            "Not found",
            request=httpx.Request("GET", "https://api.github.com/test"),
            response=httpx.Response(
                404,
                request=httpx.Request("GET", "https://api.github.com/test"),
            ),
        ),
    ):
        response = client.post(
            "/github/issue",
            json={"repo": "om-3531/DevSquad", "issue_number": 999999},
        )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_github_issue_missing_repo():
    response = client.post(
        "/github/issue",
        json={"issue_number": 1},
    )

    assert response.status_code == 422


def test_github_issue_rejects_malformed_repository_identifier():
    response = client.post(
        "/github/issue",
        json={"repo": "owner/repo/extra", "issue_number": 1},
    )

    assert response.status_code == 422


def test_github_repository_rejects_malformed_repository_identifier():
    response = client.get("/github/repository", params={"repo": "owner/repo/extra"})

    assert response.status_code == 422


def test_github_issue_does_not_expose_secrets_from_unexpected_errors():
    with patch(
        "backend.main.issue_service.get_issue",
        new_callable=AsyncMock,
        side_effect=RuntimeError("upstream failure with gsk_1234567890abcdefghijklmnop"),
    ):
        response = client.post(
            "/github/issue",
            json={"repo": "om-3531/DevSquad", "issue_number": 1},
        )

    assert response.status_code == 500
    assert "gsk_" not in response.json()["detail"]
    assert response.json()["detail"] == "Internal server error. Check server logs for diagnostic details."
