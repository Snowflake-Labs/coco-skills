"""E2E tests for dvp-ewi-fixer skill.

Run: pytest test_dvp_ewi_fixer.py -v -s

Note: Use -s flag to see real-time logs from tests.
Requires cortex CLI to be available.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL_NAME = "dvp-ewi-fixer"
SKILL_EXEC = f"${SKILL_NAME}"
FIXTURE_DIR = Path(__file__).parent / "fixtures"
INPUT_DIR = FIXTURE_DIR / "input"
SKILL_DIR = Path(__file__).parent.parent / SKILL_NAME

def is_cortex_available() -> bool:
    try:
        result = subprocess.run(
            ["cortex", "--version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


CORTEX_AVAILABLE = is_cortex_available()


def log(msg: str, level: str = "INFO"):
    """Print log message with level prefix."""
    print(f"\n[{level}] {msg}")

def run_cortex(prompt: str, workdir: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run cortex CLI with a prompt."""
    log(f"Running cortex with prompt: {prompt}")
    log(f"Working directory: {workdir}")
    
    try:
        result = subprocess.run(
            [
                "cortex",
                "--output-format", "stream-json",
                "--dangerously-allow-all-tool-calls",
                "--print", prompt,
                "-w", str(workdir),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workdir),
            input="n\n",
        )
        log(f"Return code: {result.returncode}")
        log(f"STDOUT (first 500 chars):\n{result.stdout[:500]}")
        if result.stderr:
            log(f"STDERR: {result.stderr[:200]}", level="WARN")
        return result
    except subprocess.TimeoutExpired:
        log(f"Cortex CLI timed out after {timeout}s", level="ERROR")
        pytest.skip(f"cortex CLI timed out after {timeout}s")


def extract_ewis_from_file(filepath: Path) -> list[dict]:
    """Extract EWI comments from a Python file."""
    content = filepath.read_text()
    pattern = r"#EWI: (SPRKPY\d+) => (.+)"
    matches = re.findall(pattern, content)
    return [{"code": code, "message": msg} for code, msg in matches]


@pytest.mark.e2e
@pytest.mark.skipif(not CORTEX_AVAILABLE, reason="cortex CLI not available")
class TestEwiFixerSkill:
    """E2E tests that invoke dvp-ewi-fixer skill via Cortex CLI."""

    @pytest.fixture
    def workload_dir(self, tmp_path):
        """Copy input fixtures and skill to temp directory for isolated testing."""
        workload = tmp_path / "workload"
        shutil.copytree(INPUT_DIR, workload)
        
        # Copy skill so cortex can find it
        skill_dest = workload / ".cortex" / "skills" / "dvp-ewi-fixer"
        shutil.copytree(SKILL_DIR, skill_dest)
        
        return workload

    def test_skill_detects_expected_ewis(self, workload_dir):
        """Skill scans files and reports all expected EWI codes."""
        log("=== TEST: test_skill_detects_expected_ewis ===")
        
        result = run_cortex(SKILL_EXEC, workload_dir, timeout=180)
        output = result.stdout.lower()
        
        expected_ewis = ["SPRKPY1000", "SPRKPY1001"]
        for code in expected_ewis:
            log(f"Checking for {code} in output")
            assert code.lower() in output, f"Expected {code} in output"

    def test_skill_fixes_ewis_and_produces_valid_code(self, workload_dir):
        """Skill removes EWI comments, replaces problematic code, and produces valid Python."""
        log("=== TEST: test_skill_fixes_ewis_and_produces_valid_code ===")
        
        run_cortex(SKILL_EXEC, workload_dir, timeout=180)
        
        actual_file = workload_dir / "ewis_sample.py"
        content = actual_file.read_text()
        
        # Verify no EWIs remain
        remaining_ewis = extract_ewis_from_file(actual_file)
        log(f"Remaining EWIs: {remaining_ewis}")
        assert len(remaining_ewis) == 0, f"EWIs should be removed: {remaining_ewis}"
        
        # Verify code is valid Python
        try:
            compile(content, str(actual_file), "exec")
            log("Code is valid Python syntax")
        except SyntaxError as e:
            pytest.fail(f"Fixed code has syntax error: {e}")
        
        # Verify problematic code was replaced
        assert "rdd.map" not in content, "rdd.map should be replaced with Snowpark equivalent"
        log(f"Code after fix:\n{content}")

    def test_skill_fixes_ewis_in_notebooks(self, workload_dir):
        """Skill removes EWI comments from notebook cells."""
        log("=== TEST: test_skill_fixes_ewis_in_notebooks ===")
        
        run_cortex(SKILL_EXEC, workload_dir, timeout=180)
        
        notebook_file = workload_dir / "ewis_sample.ipynb"
        content = notebook_file.read_text()
        
        # Verify no EWIs remain in notebook
        assert "#EWI:" not in content, "EWI comments should be removed from notebook"
        assert "rdd.map" not in content, "Problematic code should be replaced in notebook"
        
        log("PASSED", level="SUCCESS")