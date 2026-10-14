"""Shared fixtures for deploy-code-bundle script unit tests.

Adds both the local `scripts/` dir (prepare_code_bundle, create_code_bundle,
execute_code_bundle) and the sibling `deploy-common/scripts/` dir (sf_exec, which
the bundle scripts import) to sys.path.
"""

import os
import sys

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..", "..", "deploy-common", "scripts")))
