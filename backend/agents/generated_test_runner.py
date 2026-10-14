
import ast
import subprocess
import tempfile
from pathlib import Path


class GeneratedTestRunner:
    """Validate generated Python and run its tests inside Docker."""

    IMAGE_NAME = "devsquad-test-runner"
    TIMEOUT_SECONDS = 10
    MEMORY_LIMIT = "256m"
    CPU_LIMIT = "1"

    def run_generated_tests(
        self,
        proposed_code: str,
        generated_tests: str,
    ) -> dict:
        if not proposed_code.strip() or not generated_tests.strip():
            return {
                "status": "NOT_RUN",
                "passed": False,
                "summary": "Proposed code or generated tests are empty.",
                "return_code": None,
            }

        try:
            ast.parse(proposed_code)
            ast.parse(generated_tests)
        except SyntaxError as error:
            return {
                "status": "VALIDATION_FAILED",
                "passed": False,
                "summary": f"Invalid Python syntax: {error}",
                "return_code": None,
            }

        with tempfile.TemporaryDirectory(
            prefix="devsquad-tests-"
        ) as temp:
            workdir = Path(temp)

            (workdir / "candidate.py").write_text(
                proposed_code,
                encoding="utf-8",
            )
            (workdir / "test_candidate.py").write_text(
                generated_tests,
                encoding="utf-8",
            )

            command = [
                "docker",
                "run",
                "--rm",
                "--network", "none",
                "--memory", self.MEMORY_LIMIT,
                "--cpus", self.CPU_LIMIT,
                "--pids-limit", "64",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--mount",
                f"type=bind,source={workdir},target=/workspace,readonly",
                "--workdir", "/workspace",
                "--env", "PYTHONDONTWRITEBYTECODE=1",
                self.IMAGE_NAME,
                "python",
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "test_candidate.py",
            ]

            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {
                    "status": "TIMEOUT",
                    "passed": False,
                    "summary": (
                        "Generated tests exceeded "
                        f"{self.TIMEOUT_SECONDS} seconds."
                    ),
                    "return_code": None,
                }
            except FileNotFoundError:
                return {
                    "status": "DOCKER_ERROR",
                    "passed": False,
                    "summary": (
                        "Docker CLI was not found. "
                        "Install Docker Desktop and ensure it is running."
                    ),
                    "return_code": None,
                }

        output = (result.stdout + "\n" + result.stderr).strip()

        if result.returncode != 0 and (
            "docker daemon" in output.lower()
            or "cannot connect to the docker" in output.lower()
            or "error during connect" in output.lower()
            or "image not found" in output.lower()
            or "pull access denied" in output.lower()
        ):
            return {
                "status": "DOCKER_ERROR",
                "passed": False,
                "summary": output[-2000:] or "Docker execution failed.",
                "return_code": result.returncode,
            }

        passed = result.returncode == 0

        return {
            "status": "TESTS_PASSED" if passed else "TESTS_FAILED",
            "passed": passed,
            "summary": output[-2000:] or "No test output was produced.",
            "return_code": result.returncode,
        }
