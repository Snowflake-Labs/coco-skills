"""Pytest configuration for dvp-notebook-to-script tests.

Adds the parent directory to sys.path so that `import notebook_to_script` works.
"""

import sys
from pathlib import Path

_dvp_root = Path(__file__).resolve().parent.parent.parent.parent  # spark-migration/dvp/
sys.path.insert(0, str(_dvp_root / "dvp-notebook-to-script"))
