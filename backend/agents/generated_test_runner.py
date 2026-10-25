
import ast
import subprocess
import tempfile
from pathlib import Path


class GeneratedTestRunner:
    """Validate generated Python and run its tests inside Docker."""

    IMAGE_NAME = "devsquad-test-runner"
    TIMEOUT_SECONDS = 30
    MEMORY_LIMIT = "256m"
    CPU_LIMIT = "1"
    APPROVED_DEPENDENCIES = ("pytest", "requests", "httpx")
    DOCKER_ERROR_MARKERS = (
        "docker daemon",
        "cannot connect to the docker",
        "error during connect",
        "failed to connect to the docker",
        "dockerdesktoplinuxengine",
        "image not found",
        "unable to find image",
        "pull access denied",
        "is the docker daemon running",
    )

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

            image_error = self._ensure_image()
            if image_error:
                return {
                    "status": "DOCKER_ERROR",
                    "passed": False,
                    "summary": image_error[-2000:],
                    "return_code": None,
                }

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
                (
                    "type=bind,source="
                    f"{self._bind_source(workdir)},"
                    "target=/workspace,readonly"
                ),
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

        if self._is_docker_infra_error(output):
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

    def _bind_source(self, path: Path) -> str:
        return str(path.resolve()).replace("\\", "/")

    def _is_docker_infra_error(self, output: str) -> bool:
        text = output.lower()
        return any(marker in text for marker in self.DOCKER_ERROR_MARKERS)

    def _verify_image_dependencies(self) -> bool:
        """Verify that the existing Docker image has all required dependencies installed."""
        imports = ", ".join(self.APPROVED_DEPENDENCIES)
        try:
            check = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network", "none",
                    self.IMAGE_NAME,
                    "python",
                    "-c",
                    f"import {imports}",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            return check.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
            return False

    def _ensure_image(self) -> str | None:
        try:
            inspect = subprocess.run(
                ["docker", "image", "inspect", self.IMAGE_NAME],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (FileNotFoundError, PermissionError, OSError) as error:
            return (
                "Docker CLI could not be started. "
                "Install Docker Desktop, ensure it is running, and verify local permissions. "
                f"({type(error).__name__})"
            )
        except subprocess.TimeoutExpired:
            return "Docker image inspection timed out."

        if inspect.returncode == 0:
            if self._verify_image_dependencies():
                return None

        inspect_output = (inspect.stdout + "\n" + inspect.stderr).strip()
        if self._is_docker_infra_error(inspect_output):
            return inspect_output[-2000:] or "Docker is not available."

        root = Path(__file__).resolve().parents[2]
        dockerfile = root / "Dockerfile.test"
        try:
            build = subprocess.run(
                [
                    "docker",
                    "build",
                    "-t",
                    self.IMAGE_NAME,
                    "-f",
                    str(dockerfile),
                    str(root),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "Docker image build timed out."

        if build.returncode != 0:
            output = (build.stdout + "\n" + build.stderr).strip()
            return output[-2000:] or "Docker image build failed."

        return None
