"""Pytest configuration for dvp-entrypoint-identifier tests.

Adds skill warp packages to sys.path so tests can import the embedded WARP detector.
"""

import sys
from pathlib import Path

_dvp_root = Path(__file__).resolve().parent.parent.parent.parent  # spark-migration/dvp/

# Shared WARP lib
sys.path.insert(0, str(_dvp_root / "dvp-orchestrator"))

# Skill-specific warp package (entrypoints)
sys.path.insert(0, str(_dvp_root / "dvp-entrypoint-identifier" / "warp"))
