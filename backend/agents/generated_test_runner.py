
import ast
import subprocess
import sys
import tempfile
from pathlib import Path


class GeneratedTestRunner:
    """Validate generated Python code and run tests in a temp folder."""

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

        # Temporary storage protects project files from direct overwrites.
        # It is not a security sandbox for untrusted Python code.
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

            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "test_candidate.py",
                        "-q",
                    ],
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {
                    "status": "TIMEOUT",
                    "passed": False,
                    "summary": "Generated tests exceeded 10 seconds.",
                    "return_code": None,
                }

        passed = result.returncode == 0
        output = (result.stdout + "\n" + result.stderr).strip()

        return {
            "status": "TESTS_PASSED" if passed else "TESTS_FAILED",
            "passed": passed,
            "summary": output[-2000:] or "No test output was produced.",
            "return_code": result.returncode,
        }
