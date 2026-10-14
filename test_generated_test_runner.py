
from backend.agents.generated_test_runner import GeneratedTestRunner


def test_valid_python_is_accepted():
    runner = GeneratedTestRunner()

    result = runner.run_generated_tests(
        proposed_code=(
            "def check_even_odd(number):\n"
            "    return 'Even' if number % 2 == 0 else 'Odd'\n"
        ),
        generated_tests=(
            "from candidate import check_even_odd\n\n"
            "def test_even():\n"
            "    assert check_even_odd(4) == 'Even'\n\n"
            "def test_odd():\n"
            "    assert check_even_odd(3) == 'Odd'\n"
        ),
    )

    assert result["status"] == "TESTS_PASSED"
    assert result["passed"] is True


def test_invalid_python_is_rejected():
    runner = GeneratedTestRunner()

    result = runner.run_generated_tests(
        proposed_code="def broken(:\n    pass",
        generated_tests="def test_example():\n    assert True\n",
    )

    assert result["status"] == "VALIDATION_FAILED"
    assert result["passed"] is False


def test_empty_code_is_not_run():
    runner = GeneratedTestRunner()

    result = runner.run_generated_tests(
        proposed_code="",
        generated_tests="def test_example():\n    assert True\n",
    )

    assert result["status"] == "NOT_RUN"
    assert result["passed"] is False

def test_failing_generated_tests_are_reported():
    runner = GeneratedTestRunner()

    result = runner.run_generated_tests(
        proposed_code="def answer():\n    return 1\n",
        generated_tests=(
            "from candidate import answer\n\n"
            "def test_answer():\n"
            "    assert answer() == 2\n"
        ),
    )

    assert result["status"] == "TESTS_FAILED"
    assert result["passed"] is False
    assert result["return_code"] != 0


def test_generated_tests_timeout(monkeypatch):
    import subprocess

    runner = GeneratedTestRunner()

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd="pytest",
            timeout=10,
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.run_generated_tests(
        proposed_code="def answer():\n    return 1\n",
        generated_tests="def test_answer():\n    assert True\n",
    )

    assert result["status"] == "TIMEOUT"
    assert result["passed"] is False
    assert result["return_code"] is None
