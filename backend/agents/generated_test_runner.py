
import ast
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class GeneratedTestRunner:
    """Validate generated Python and run its tests inside Docker."""

    IMAGE_NAME = "devsquad-test-runner"
    TIMEOUT_SECONDS = 30
    MEMORY_LIMIT = "256m"
    CPU_LIMIT = "1"
    APPROVED_DEPENDENCIES = ("pytest", "pytest_asyncio", "requests", "httpx", "dotenv")
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
        "error response from daemon",
        "daemon is not running",
        "pipe/docker",
    )

    def run_generated_tests(
        self,
        proposed_code: str,
        generated_tests: str,
        target_path: str = "",
    ) -> dict:
        if not proposed_code.strip() or not generated_tests.strip():
            return {
                "status": "NOT_RUN",
                "passed": False,
                "summary": "Proposed code or generated tests are empty.",
                "return_code": None,
                "stdout": "",
                "stderr": "",
                "failed_tests": [],
                "tracebacks": "",
                "infrastructure_error": False,
                "timed_out": False,
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
                "stdout": "",
                "stderr": str(error),
                "failed_tests": [],
                "tracebacks": "",
                "infrastructure_error": False,
                "timed_out": False,
            }

        with tempfile.TemporaryDirectory(
            prefix="devsquad-tests-"
        ) as temp:
            workdir = Path(temp)

            # Copy project backend package so candidate can reuse existing project architecture
            project_root = Path(__file__).resolve().parents[2]
            backend_src = project_root / "backend"
            if backend_src.is_dir():
                try:
                    shutil.copytree(
                        backend_src,
                        workdir / "backend",
                        ignore=shutil.ignore_patterns(
                            "__pycache__", "*.pyc", "proposals.sqlite3", "*.sqlite3", "*.db"
                        ),
                    )
                except Exception as copy_err:
                    logger.warning("Could not copy backend into sandbox: %s", copy_err)

            # Write pytest.ini with asyncio auto-discovery
            (workdir / "pytest.ini").write_text(
                "[pytest]\nasyncio_mode = auto\n",
                encoding="utf-8",
            )

            # Write conftest.py to ensure validate_repository is available in sandbox
            (workdir / "conftest.py").write_text(
                "import builtins\n"
                "try:\n"
                "    from backend.github.validation import validate_repository\n"
                "    builtins.validate_repository = validate_repository\n"
                "except Exception:\n"
                "    pass\n",
                encoding="utf-8",
            )

            (workdir / "candidate.py").write_text(
                proposed_code,
                encoding="utf-8",
            )

            if target_path and not Path(target_path).is_absolute() and ".." not in target_path:
                target_file = workdir / target_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(proposed_code, encoding="utf-8")

            # Sanitize any accidental builtins.validate_repository references
            sanitized_tests = re.sub(
                r'patch\.object\s*\(\s*builtins\s*,\s*["\']validate_repository["\']',
                'patch("candidate.validate_repository"',
                generated_tests,
            )
            sanitized_tests = re.sub(
                r'patch\s*\(\s*["\']builtins\.validate_repository["\']',
                'patch("candidate.validate_repository"',
                sanitized_tests,
            )
            sanitized_tests = re.sub(
                r'builtins\.validate_repository',
                'candidate.validate_repository',
                sanitized_tests,
            )
            sanitized_tests = re.sub(
                r'patch\.object\s*\(\s*validate_repository\s*,\s*["\']__call__["\']',
                'patch("candidate.validate_repository"',
                sanitized_tests,
            )

            (workdir / "test_candidate.py").write_text(
                sanitized_tests,
                encoding="utf-8",
            )

            image_error = self._ensure_image()
            if image_error:
                return {
                    "status": "DOCKER_ERROR",
                    "passed": False,
                    "summary": image_error[-2000:],
                    "return_code": None,
                    "stdout": "",
                    "stderr": image_error,
                    "failed_tests": [],
                    "tracebacks": "",
                    "infrastructure_error": True,
                    "timed_out": False,
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
                "--env", "PYTHONPATH=/workspace",
                self.IMAGE_NAME,
                "python",
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-o",
                "asyncio_mode=auto",
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
                    "stdout": "",
                    "stderr": f"Tests timed out after {self.TIMEOUT_SECONDS}s.",
                    "failed_tests": [],
                    "tracebacks": "",
                    "infrastructure_error": False,
                    "timed_out": True,
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
                    "stdout": "",
                    "stderr": "Docker CLI executable not found on host PATH.",
                    "failed_tests": [],
                    "tracebacks": "",
                    "infrastructure_error": True,
                    "timed_out": False,
                }

        output = (result.stdout + "\n" + result.stderr).strip()

        if self._is_docker_infra_error(output) or result.returncode == 125:
            return {
                "status": "DOCKER_ERROR",
                "passed": False,
                "summary": output[-2000:] or "Docker execution failed.",
                "return_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "failed_tests": [],
                "tracebacks": "",
                "infrastructure_error": True,
                "timed_out": False,
            }

        passed = result.returncode == 0
        failed_tests = self._extract_failed_tests(output)
        tracebacks = self._extract_tracebacks(output)

        return {
            "status": "TESTS_PASSED" if passed else "TESTS_FAILED",
            "passed": passed,
            "summary": output[-2000:] or "No test output was produced.",
            "return_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "failed_tests": failed_tests,
            "tracebacks": tracebacks,
            "infrastructure_error": False,
            "timed_out": False,
        }

    def _extract_failed_tests(self, output: str) -> list[str]:
        """Extract failed test names from pytest output."""
        matches = re.findall(r"FAILED\s+[^:\s]+::([a-zA-Z0-9_]+)", output)
        if not matches:
            matches = re.findall(r"_{3,}\s+([a-zA-Z0-9_]+)\s+_{3,}", output)
        return list(dict.fromkeys(matches))

    def _extract_tracebacks(self, output: str) -> str:
        """Extract relevant failure traceback blocks from pytest output."""
        if "FAILURES" in output or "ERRORS" in output:
            parts = re.split(r"={3,}\s+(?:FAILURES|ERRORS)\s+={3,}", output, maxsplit=1)
            if len(parts) > 1:
                tail = parts[1]
                end_parts = re.split(r"={3,}\s+short test summary info\s+={3,}", tail, maxsplit=1)
                return end_parts[0].strip()[:3000]
        return ""


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
