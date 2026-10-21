from unittest.mock import MagicMock, patch

from backend.agents.reviewer import ReviewerAgent


def make_reviewer():
    reviewer = ReviewerAgent.__new__(ReviewerAgent)
    reviewer.model = "test-model"
    reviewer.client = MagicMock()
    return reviewer


def _completion(content, finish_reason="stop"):
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    return response


def test_reviewer_retries_missing_verdict():
    reviewer = make_reviewer()
    reviewer.client.chat.completions.create.side_effect = [
        _completion("Looks fine overall.", finish_reason="length"),
        _completion("VERDICT: APPROVE\nIssues: None\nFixes: None"),
    ]

    with patch("backend.agents.reviewer.time.sleep"):
        result = reviewer.review_code(
            task="even odd",
            code="def check_even_odd(number):\n    return 'Even'\n",
        )

    assert "VERDICT: APPROVE" in result
    assert reviewer.client.chat.completions.create.call_count == 2


def test_reviewer_requests_changes_when_retry_has_no_verdict():
    reviewer = make_reviewer()
    reviewer.client.chat.completions.create.return_value = _completion("")

    with patch("backend.agents.reviewer.time.sleep"):
        result = reviewer.review_code(task="task", code="print(1)")

    assert "VERDICT: REQUEST_CHANGES" in result
