
import subprocess
import sys
from pathlib import Path


class TestRunner:
    def run_tests(self, test_file: str) -> dict:
        project_root = Path(__file__).resolve().parents[2]
        test_path = (project_root / test_file).resolve()

        if not test_path.is_relative_to(project_root):
            raise ValueError("Test file must be inside the project.")

        if not test_path.is_file() or test_path.suffix != ".py":
            raise ValueError("A valid Python test file is required.")

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(test_path),
                    "-v",
                ],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )

            return {
                "passed": result.returncode == 0,
                "return_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "return_code": None,
                "stdout": "",
                "stderr": "Tests timed out after 30 seconds.",
            }
