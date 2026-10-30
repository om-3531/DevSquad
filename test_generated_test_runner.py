
import shutil
import subprocess

import pytest

from backend.agents.generated_test_runner import GeneratedTestRunner


def test_valid_python_is_accepted(monkeypatch):
    import subprocess
    runner = GeneratedTestRunner()
    monkeypatch.setattr(runner, "_ensure_image", lambda: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="2 passed", stderr=""),
    )

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


def test_docker_connectivity_error_is_not_a_test_failure(monkeypatch):
    import subprocess

    runner = GeneratedTestRunner()

    def fake_run(command, *args, **kwargs):
        completed = subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr=(
                "failed to connect to the docker API at "
                "npipe:////./pipe/dockerDesktopLinuxEngine"
            ),
        )
        return completed

    monkeypatch.setattr(runner, "_ensure_image", lambda: None)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.run_generated_tests(
        proposed_code="def answer():\n    return 1\n",
        generated_tests="def test_answer():\n    assert True\n",
    )

    assert result["status"] == "DOCKER_ERROR"
    assert result["passed"] is False


def test_docker_permission_error_is_reported_as_an_environment_error(monkeypatch):
    import subprocess

    runner = GeneratedTestRunner()

    def fail_to_start(*args, **kwargs):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(subprocess, "run", fail_to_start)

    result = runner.run_generated_tests(
        proposed_code="def answer():\n    return 1\n",
        generated_tests="def test_answer():\n    assert True\n",
    )

    assert result["status"] == "DOCKER_ERROR"
    assert result["passed"] is False
    assert "PermissionError" in result["summary"]


def test_docker_exit_code_125_is_reported_as_infrastructure_error(monkeypatch):
    import subprocess

    runner = GeneratedTestRunner()

    def fake_run(command, *args, **kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=125,
            stdout="",
            stderr="docker: Error response from daemon: container failed to initialize.",
        )

    monkeypatch.setattr(runner, "_ensure_image", lambda: None)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.run_generated_tests(
        proposed_code="def answer():\n    return 42\n",
        generated_tests="def test_answer():\n    assert True\n",
    )

    assert result["status"] == "DOCKER_ERROR"
    assert result["passed"] is False
    assert result["infrastructure_error"] is True
    assert result["return_code"] == 125


def test_docker_daemon_error_marker_reported_as_infrastructure_error(monkeypatch):
    import subprocess

    runner = GeneratedTestRunner()

    def fake_run(command, *args, **kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr="error: open //./pipe/docker: The system cannot find the file specified.",
        )

    monkeypatch.setattr(runner, "_ensure_image", lambda: None)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.run_generated_tests(
        proposed_code="def answer():\n    return 42\n",
        generated_tests="def test_answer():\n    assert True\n",
    )

    assert result["status"] == "DOCKER_ERROR"
    assert result["passed"] is False
    assert result["infrastructure_error"] is True


def test_empty_code_is_not_run():
    runner = GeneratedTestRunner()

    result = runner.run_generated_tests(
        proposed_code="",
        generated_tests="def test_example():\n    assert True\n",
    )

    assert result["status"] == "NOT_RUN"
    assert result["passed"] is False

def test_failing_generated_tests_are_reported(monkeypatch):
    import subprocess
    runner = GeneratedTestRunner()
    monkeypatch.setattr(runner, "_ensure_image", lambda: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=1, stdout="1 failed", stderr=""),
    )

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

    monkeypatch.setattr(runner, "_ensure_image", lambda: None)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.run_generated_tests(
        proposed_code="def answer():\n    return 1\n",
        generated_tests="def test_answer():\n    assert True\n",
    )

    assert result["status"] == "TIMEOUT"
    assert result["passed"] is False
    assert result["return_code"] is None


def test_candidate_with_requests_dependency(monkeypatch):
    import subprocess
    runner = GeneratedTestRunner()
    monkeypatch.setattr(runner, "_ensure_image", lambda: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="1 passed", stderr=""),
    )

    result = runner.run_generated_tests(
        proposed_code=(
            "import requests\n\n"
            "def fetch_status():\n"
            "    return requests.get('https://example.com').status_code\n"
        ),
        generated_tests=(
            "from unittest.mock import patch, MagicMock\n"
            "from candidate import fetch_status\n\n"
            "def test_fetch_status():\n"
            "    with patch('candidate.requests.get') as mock_get:\n"
            "        mock_resp = MagicMock()\n"
            "        mock_resp.status_code = 200\n"
            "        mock_get.return_value = mock_resp\n"
            "        assert fetch_status() == 200\n"
        ),
    )

    assert result["status"] == "TESTS_PASSED"
    assert result["passed"] is True


def test_candidate_with_httpx_dependency(monkeypatch):
    import subprocess
    runner = GeneratedTestRunner()
    monkeypatch.setattr(runner, "_ensure_image", lambda: None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="1 passed", stderr=""
        ),
    )

    result = runner.run_generated_tests(
        proposed_code=(
            "import httpx\n\n"
            "def fetch_status(url: str = 'https://example.com') -> int:\n"
            "    with httpx.Client() as client:\n"
            "        response = client.get(url)\n"
            "        return response.status_code\n"
        ),
        generated_tests=(
            "from unittest.mock import patch, MagicMock\n"
            "from candidate import fetch_status\n\n"
            "def test_fetch_status():\n"
            "    with patch('candidate.httpx.Client') as mock_client_cls:\n"
            "        mock_client = MagicMock()\n"
            "        mock_resp = MagicMock()\n"
            "        mock_resp.status_code = 200\n"
            "        mock_client.get.return_value = mock_resp\n"
            "        mock_client.__enter__.return_value = mock_client\n"
            "        mock_client.__exit__.return_value = None\n"
            "        mock_client_cls.return_value = mock_client\n"
            "        assert fetch_status() == 200\n"
        ),
    )

    assert result["status"] == "TESTS_PASSED"
    assert result["passed"] is True


def test_approved_dependencies_list():
    runner = GeneratedTestRunner()
    assert "httpx" in runner.APPROVED_DEPENDENCIES
    assert "requests" in runner.APPROVED_DEPENDENCIES
    assert "pytest" in runner.APPROVED_DEPENDENCIES


def test_candidate_requirements_file_exists_and_contains_approved_dependencies():
    from pathlib import Path
    req_file = Path(__file__).resolve().parent / "candidate-requirements.txt"
    assert req_file.exists(), "candidate-requirements.txt must exist"
    content = req_file.read_text(encoding="utf-8")
    assert "httpx==" in content
    assert "requests==" in content
    assert "pytest==" in content


def test_verify_image_dependencies_returns_true_when_healthy(monkeypatch):
    import subprocess
    runner = GeneratedTestRunner()
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr=""
        ),
    )
    assert runner._verify_image_dependencies() is True


def test_verify_image_dependencies_returns_false_when_missing(monkeypatch):
    import subprocess
    runner = GeneratedTestRunner()
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="ModuleNotFoundError: No module named 'httpx'"
        ),
    )
    assert runner._verify_image_dependencies() is False


def test_runner_builds_a_hardened_docker_command(monkeypatch):
    runner = GeneratedTestRunner()
    commands = []
    monkeypatch.setattr(runner, "_ensure_image", lambda: None)

    def fake_run(command, *args, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="1 passed",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.run_generated_tests(
        proposed_code="def answer():\n    return 42\n",
        generated_tests="from candidate import answer\n\ndef test_answer():\n    assert answer() == 42\n",
    )

    command = commands[0]
    assert result["status"] == "TESTS_PASSED"
    assert command[:3] == ["docker", "run", "--rm"]
    assert ["--network", "none"] == command[3:5]
    assert "--read-only" in command
    assert ["--memory", "256m"] == command[5:7]
    assert ["--cpus", "1"] == command[7:9]
    assert ["--pids-limit", "64"] == command[9:11]
    assert "no-new-privileges" in command
    assert "readonly" in command[command.index("--mount") + 1]


@pytest.mark.integration
def test_real_docker_runner_executes_harmless_candidate_and_removes_container():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not available on PATH.")

    runner = GeneratedTestRunner()
    before = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"ancestor={runner.IMAGE_NAME}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert before.returncode == 0

    result = runner.run_generated_tests(
        proposed_code="def answer():\n    return 42\n",
        generated_tests="from candidate import answer\n\ndef test_answer():\n    assert answer() == 42\n",
    )

    assert result["status"] == "TESTS_PASSED", result["summary"]
    assert result["passed"] is True

    after = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"ancestor={runner.IMAGE_NAME}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert after.returncode == 0
    assert after.stdout.split() == before.stdout.split()


@pytest.mark.integration
def test_real_docker_runner_executes_candidate_with_httpx_and_requests():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is not available on PATH.")

    runner = GeneratedTestRunner()
    result = runner.run_generated_tests(
        proposed_code=(
            "import httpx\n"
            "import requests\n\n"
            "def check_env():\n"
            "    return bool(httpx.__version__ and requests.__version__)\n"
        ),
        generated_tests=(
            "from candidate import check_env\n\n"
            "def test_env():\n"
            "    assert check_env() is True\n"
        ),
    )

    assert result["status"] == "TESTS_PASSED", result["summary"]
    assert result["passed"] is True

