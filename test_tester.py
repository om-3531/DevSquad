from unittest.mock import MagicMock, patch

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
