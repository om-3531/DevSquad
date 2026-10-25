from unittest.mock import MagicMock, patch
import pytest

from backend.agents.coder import CoderAgent


def make_coder():
    coder = CoderAgent.__new__(CoderAgent)
    coder.model = "test-model"
    coder.client = MagicMock()
    return coder


def _completion(content, finish_reason="stop"):
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    return response


def test_coder_generate_valid_code():
    coder = make_coder()
    valid_code = "def add(a, b):\n    return a + b\n"
    coder.client.chat.completions.create.return_value = _completion(valid_code)

    result = coder.generate_code(
        task="Write an add function",
        implementation_plan="1. Define add",
    )

    assert result == valid_code.strip()
    assert coder.client.chat.completions.create.call_count == 1


def test_coder_retries_truncated_output():
    coder = make_coder()
    truncated = "def add(a, b):\n    ret"
    complete = "def add(a, b):\n    return a + b\n"

    coder.client.chat.completions.create.side_effect = [
        _completion(truncated, finish_reason="length"),
        _completion(complete, finish_reason="stop"),
    ]

    with patch("backend.agents.coder.time.sleep"):
        result = coder.generate_code(
            task="Write an add function",
            implementation_plan="1. Define add",
        )

    assert result == complete.strip()
    assert coder.client.chat.completions.create.call_count == 2


def test_coder_retries_syntax_error():
    coder = make_coder()
    bad_syntax = "def add(a, b:\n    return a + b\n"
    good_syntax = "def add(a, b):\n    return a + b\n"

    coder.client.chat.completions.create.side_effect = [
        _completion(bad_syntax, finish_reason="stop"),
        _completion(good_syntax, finish_reason="stop"),
    ]

    with patch("backend.agents.coder.time.sleep"):
        result = coder.generate_code(
            task="Write an add function",
            implementation_plan="1. Define add",
        )

    assert result == good_syntax.strip()
    assert coder.client.chat.completions.create.call_count == 2


def test_coder_retries_empty_response():
    coder = make_coder()
    good_syntax = "def add(a, b):\n    return a + b\n"

    coder.client.chat.completions.create.side_effect = [
        _completion("", finish_reason="stop"),
        _completion(good_syntax, finish_reason="stop"),
    ]

    with patch("backend.agents.coder.time.sleep"):
        result = coder.generate_code(
            task="Write an add function",
            implementation_plan="1. Define add",
        )

    assert result == good_syntax.strip()
    assert coder.client.chat.completions.create.call_count == 2


def test_coder_raises_value_error_after_max_retries():
    coder = make_coder()
    bad_syntax = "def broken(:\n    pass"

    coder.client.chat.completions.create.return_value = _completion(bad_syntax)

    with patch("backend.agents.coder.time.sleep"):
        with pytest.raises(ValueError, match="invalid Python syntax"):
            coder.generate_code(
                task="Write broken code",
                implementation_plan="1. Do nothing",
            )


def test_coder_improve_code_passes_context():
    coder = make_coder()
    improved = "def add(a: int, b: int) -> int:\n    return a + b\n"
    coder.client.chat.completions.create.return_value = _completion(improved)

    result = coder.improve_code(
        task="Add type hints",
        implementation_plan="1. Add type hints to add",
        previous_code="def add(a, b):\n    return a + b\n",
        reviewer_feedback="Please add type hints.",
        existing_code="def old(): pass",
    )

    assert result == improved.strip()
    call_prompt = coder.client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Please add type hints." in call_prompt
    assert "def old(): pass" in call_prompt


def test_coder_retries_empty_content_with_finish_reason_length():
    coder = make_coder()
    valid_code = "def subtract(a, b):\n    return a - b\n"

    # First attempt: empty content with finish_reason='length' (e.g. reasoning tokens consumed budget)
    # Second attempt: complete code with finish_reason='stop'
    coder.client.chat.completions.create.side_effect = [
        _completion("", finish_reason="length"),
        _completion(valid_code, finish_reason="stop"),
    ]

    with patch("backend.agents.coder.time.sleep"):
        result = coder.generate_code(
            task="Write a subtract function",
            implementation_plan="1. Define subtract",
        )

    assert result == valid_code.strip()
    assert coder.client.chat.completions.create.call_count == 2
    # Check that retry prompt notified coder of previous truncation before code generation
    second_call_prompt = coder.client.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"]
    assert "truncated before producing code" in second_call_prompt


def test_coder_empty_content_length_exhaustion():
    coder = make_coder()
    coder.client.chat.completions.create.return_value = _completion("", finish_reason="length")

    with patch("backend.agents.coder.time.sleep"):
        with pytest.raises(ValueError, match="truncated by the token limit before code generation"):
            coder.generate_code(
                task="Write code",
                implementation_plan="1. Do task",
            )

    assert coder.client.chat.completions.create.call_count == 3


def test_coder_truncated_code_exhaustion():
    coder = make_coder()
    coder.client.chat.completions.create.return_value = _completion("def f():\n    re", finish_reason="length")

    with patch("backend.agents.coder.time.sleep"):
        with pytest.raises(ValueError, match="truncated by the token limit"):
            coder.generate_code(
                task="Write code",
                implementation_plan="1. Do task",
            )

    assert coder.client.chat.completions.create.call_count == 3


def test_coder_safe_logging_token_usage(caplog):
    import logging
    coder = make_coder()
    valid_code = "def multiply(a, b):\n    return a * b\n"
    response = _completion(valid_code, finish_reason="stop")
    usage = MagicMock()
    usage.prompt_tokens = 45
    usage.completion_tokens = 25
    usage.total_tokens = 70
    usage.completion_tokens_details = MagicMock(reasoning_tokens=5)
    response.usage = usage
    coder.client.chat.completions.create.return_value = response

    with caplog.at_level(logging.INFO):
        result = coder.generate_code(task="Multiply", implementation_plan="Plan")

    assert result == valid_code.strip()
    # Ensure token usage and model are logged safely without secrets
    assert any("Groq completion: model=test-model" in record.message for record in caplog.records)
    assert any("prompt_tokens=45" in record.message for record in caplog.records)
    assert any("completion_tokens=25" in record.message for record in caplog.records)
    assert any("reasoning_tokens=5" in record.message for record in caplog.records)

