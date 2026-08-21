"""
Shared test configuration for stage-conversion tests.

This conftest.py adds the skill's scripts directory to sys.path so all tests
can import from embedded_path_replacer without duplicating the path setup.
"""

import sys
from pathlib import Path

import pytest

# Add the stage-conversion scripts directory to path for imports
REPO_ROOT = Path(__file__).parent.parent.parent
SKILL_SCRIPTS = REPO_ROOT / "snowpark-api" / "stage-conversion" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))


@pytest.fixture
def skill_scripts_path() -> Path:
    """Path to the stage-conversion scripts directory."""
    return SKILL_SCRIPTS


@pytest.fixture
def repo_root() -> Path:
    """Repository root path."""
    return REPO_ROOT
