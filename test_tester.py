from unittest.mock import MagicMock, patch
import httpx
import pytest
from groq import RateLimitError

from backend.agents.tester import TesterAgent


def make_tester():
    tester = TesterAgent.__new__(TesterAgent)
    tester.model = "test-model"
    tester.client = MagicMock()
    return tester


def _completion(content, finish_reason="stop"):
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    return response


def _rate_limit_error(message="Rate limit reached", headers=None):
    req = httpx.Request("POST", "https://api.groq.com")
    resp = httpx.Response(429, headers=headers or {}, request=req)
    return RateLimitError(message=message, response=resp, body=None)


def test_generate_tests_retries_truncated_output():
    tester = make_tester()
    truncated = "from candidate import check_even_odd\n\ndef test_even():\n    assert check_even_odd(2) == 'Even'\n\ndef test_odd():\n    asser"
    complete = (
        "from candidate import check_even_odd\n\n"
        "def test_even():\n"
        "    assert check_even_odd(2) == 'Even'\n"
    )
    tester.client.chat.completions.create.side_effect = [
        _completion(truncated, finish_reason="length"),
        _completion(complete, finish_reason="stop"),
    ]

    with patch("backend.agents.tester.time.sleep"):
        result = tester.generate_tests(
            task="even odd",
            proposed_code="def check_even_odd(number):\n    return 'Even'\n",
        )

    assert "def test_even" in result
    assert tester.client.chat.completions.create.call_count == 2


def test_generate_tests_rejects_backend_imports():
    tester = make_tester()
    tester.client.chat.completions.create.side_effect = [
        _completion("from backend.main import app\n\ndef test_app():\n    assert app\n"),
        _completion("from candidate import check_even_odd\n\ndef test_even():\n    assert check_even_odd(2) == 'Even'\n"),
    ]

    with patch("backend.agents.tester.time.sleep"):
        result = tester.generate_tests(
            task="even odd",
            proposed_code="def check_even_odd(number):\n    return 'Even'\n",
        )

    assert "backend" not in result
    assert tester.client.chat.completions.create.call_count == 2


def test_generate_tests_rate_limit_with_retry_after_header():
    tester = make_tester()
    complete = (
        "from candidate import check_even_odd\n\n"
        "def test_even():\n"
        "    assert check_even_odd(2) == 'Even'\n"
    )
    rl_err = _rate_limit_error(headers={"retry-after": "2.5"})
    tester.client.chat.completions.create.side_effect = [
        rl_err,
        _completion(complete),
    ]

    with patch("backend.agents.tester.time.sleep") as mock_sleep:
        result = tester.generate_tests(
            task="even odd",
            proposed_code="def check_even_odd(number):\n    return 'Even'\n",
        )

    assert "def test_even" in result
    assert tester.client.chat.completions.create.call_count == 2
    mock_sleep.assert_called_once_with(3.0)  # 2.5 + 0.5 buffer


def test_generate_tests_rate_limit_with_message_parse():
    tester = make_tester()
    complete = (
        "from candidate import check_even_odd\n\n"
        "def test_even():\n"
        "    assert check_even_odd(2) == 'Even'\n"
    )
    rl_err = _rate_limit_error(
        message="Rate limit reached for model openai/gpt-oss-20b on tokens per minute (TPM). Please try again in 4.0s."
    )
    tester.client.chat.completions.create.side_effect = [
        rl_err,
        _completion(complete),
    ]

    with patch("backend.agents.tester.time.sleep") as mock_sleep:
        result = tester.generate_tests(
            task="even odd",
            proposed_code="def check_even_odd(number):\n    return 'Even'\n",
        )

    assert "def test_even" in result
    assert tester.client.chat.completions.create.call_count == 2
    mock_sleep.assert_called_once_with(4.5)  # 4.0 + 0.5 buffer


def test_generate_tests_rate_limit_exponential_backoff():
    tester = make_tester()
    complete = (
        "from candidate import check_even_odd\n\n"
        "def test_even():\n"
        "    assert check_even_odd(2) == 'Even'\n"
    )
    rl_err = _rate_limit_error(message="Rate limit hit")
    tester.client.chat.completions.create.side_effect = [
        rl_err,
        _completion(complete),
    ]

    with patch("backend.agents.tester.time.sleep") as mock_sleep:
        result = tester.generate_tests(
            task="even odd",
            proposed_code="def check_even_odd(number):\n    return 'Even'\n",
        )

    assert "def test_even" in result
    assert tester.client.chat.completions.create.call_count == 2
    mock_sleep.assert_called_once_with(2.0)  # 2.0 * (2^0)


def test_generate_tests_rate_limit_exhausted():
    tester = make_tester()
    rl_err = _rate_limit_error(message="Persistent rate limit")
    tester.client.chat.completions.create.side_effect = rl_err

    with patch("backend.agents.tester.time.sleep") as mock_sleep:
        with pytest.raises(RateLimitError):
            tester.generate_tests(
                task="even odd",
                proposed_code="def check_even_odd(number):\n    return 'Even'\n",
            )

    assert tester.client.chat.completions.create.call_count == 3
    assert mock_sleep.call_count == 2
