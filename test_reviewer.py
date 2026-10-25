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


def test_reviewer_deterministic_syntax_error_catches_invalid_syntax():
    reviewer = make_reviewer()
    # Code with a syntax error (e.g., stray token or incomplete function)
    invalid_code = "def incomplete_func():\n    return re\n    if not isinsta"

    result = reviewer.review_code(task="task", code=invalid_code)

    assert "VERDICT: REQUEST_CHANGES" in result
    assert "syntax error" in result.lower()
    # Ensure LLM was not called at all since AST check caught it deterministically
    assert reviewer.client.chat.completions.create.call_count == 0


def test_reviewer_passes_full_code_without_truncation():
    reviewer = make_reviewer()
    # Code longer than 4000 characters
    long_code = "# " + ("x" * 6000) + "\ndef valid_func(): pass\n"

    prompt = reviewer._build_prompt(task="task", code=long_code, plan="plan", target_path="src/app.py")

    assert len(prompt) > 6000
    assert ("x" * 6000) in prompt
    assert "def valid_func(): pass" in prompt


def test_reviewer_sees_test_file_and_coverage():
    reviewer = make_reviewer()
    prompt = reviewer._build_prompt(
        task="task",
        code="def hello(): pass",
        plan="plan",
        target_path="backend/github/issues.py",
        test_code="def test_create_issue(): assert True",
        test_path="test_github_client.py",
    )
    assert "Associated test file (test_github_client.py):" in prompt
    assert "test_create_issue" in prompt

