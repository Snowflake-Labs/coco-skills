"""Shared fixtures for deploy-notebook script unit tests.

Adds both the local `scripts/` dir (create_notebook, execute_notebook,
prepare_validation) and the sibling `deploy-common/scripts/` dir (sf_exec, which
the notebook scripts import) to sys.path.
"""

import os
import sys

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..", "..", "deploy-common", "scripts")))
