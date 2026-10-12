
import subprocess
import sys
import re
from pathlib import Path


class TestRunner:
    def run_tests(self, test_file: str) -> dict:
        project_root = Path(__file__).resolve().parents[2]
        test_path = (project_root / test_file).resolve()

        # Allow only Python test files inside the project.
        if not test_path.is_relative_to(project_root):
            raise ValueError(
                "Test file must be inside the project."
            )

        if not test_path.is_file() or test_path.suffix != ".py":
            raise ValueError(
                "A valid Python test file is required."
            )

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(test_path),
                    "-v",
                    "--tb=short",
                ],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )

            output = result.stdout + "\n" + result.stderr

            # Extract the final pytest summary.
            summary_match = re.search(
                r"(\d+ passed)?"
                r"(?:,\s*)?(\d+ failed)?"
                r"(?:,\s*)?(\d+ skipped)?"
                r"(?:,\s*)?(\d+ error(?:s)?)?"
                r"\s+in\s+[\d.]+s",
                output,
            )

            summary = (
                summary_match.group(0).strip()
                if summary_match
                else "Test summary unavailable"
            )

            return {
                "passed": result.returncode == 0,
                "return_code": result.returncode,
                "summary": summary,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "return_code": None,
                "summary": "Test execution timed out",
                "stdout": "",
                "stderr": "Tests timed out after 30 seconds.",
            }
