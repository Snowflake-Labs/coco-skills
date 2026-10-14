"""
Shared fixtures for sma-dashboard-generator tests.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add the skill's scripts directory to the path for imports
SKILL_DIR = (
    Path(__file__).parent.parent.parent / "snowpark-api" / "sma-dashboard-generator"
)
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def fixtures_dir() -> Path:
    """Return the path to the fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_csv_path(fixtures_dir: Path) -> Path:
    """Return the path to the sample Issues.csv file."""
    return fixtures_dir / "sample_issues.csv"


@pytest.fixture
def temp_output_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_ewi_records() -> list[dict]:
    """Return a list of sample EWI records for testing aggregation."""
    return [
        {'code': 'SPRKPY-1001', 'description': 'Test warning', 'category': 'Conversion', 'file_id': 'src/main.py', 'line': '10', 'column': '5'},
        {'code': 'SPRKPY-1001', 'description': 'Test warning', 'category': 'Conversion', 'file_id': 'src/utils.py', 'line': '20', 'column': '10'},
        {'code': 'SPRKPY-1001', 'description': 'Test warning', 'category': 'Conversion', 'file_id': 'src/main.py', 'line': '15', 'column': '3'},
        {'code': 'SSC-EWI-0001', 'description': 'SQL issue', 'category': 'SQL', 'file_id': 'queries/test.sql', 'line': '5', 'column': '1'},
    ]


@pytest.fixture
def sample_aggregated_ewis() -> list[dict]:
    """Return sample aggregated EWIs for testing summary generation."""
    return [
        {'code': 'SPRKPY-1001', 'status': 'pending', 'occurrences': 3},
        {'code': 'SSC-EWI-0001', 'status': 'manual_resolved', 'occurrences': 2},
        {'code': 'SSC-FDM-0005', 'status': 'in_progress', 'occurrences': 1},
        {'code': 'SPRKPY-2002', 'status': 'wont_fix', 'occurrences': 1},
    ]
