"""
Import Handler Mixin - Handle import statement analysis.

This mixin provides methods for tracking and classifying Python imports
in PySpark code.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


class ImportHandlerMixin:
    """
    Mixin for handling import statements in AST parsing.

    Provides:
    - visit_Import: Handle `import X` statements
    - visit_ImportFrom: Handle `from X import Y` statements
    - _classify_import: Classify modules as external/custom/local
    - _is_submodule_import: Check if an import is a submodule
    - _check_source_available: Check if source code exists in workload
    """

    # Attributes expected from the main parser class
    imports: dict[str, dict[str, Any]]
    _workload_root: Path | None  # Root directory for source detection

    # Known submodules (not classes/functions)
    # Only include modules that users commonly import with aliases
    _KNOWN_SUBMODULES = {
        "pyspark.sql": {"functions", "types", "window"},
        "pyspark": {"sql", "ml", "streaming", "rdd", "context"},
        "pandas": {"core", "io"},
        "numpy": {"linalg", "random", "fft"},
    }

    # Known PySpark modules for classification
    _PYSPARK_MODULES = {
        "pyspark",
        "pyspark.sql",
        "pyspark.sql.functions",
        "pyspark.sql.types",
        "pyspark.sql.window",
        "pyspark.ml",
        "pyspark.streaming",
    }

    _STDLIB_MODULES = {
        # Core modules
        "os",
        "sys",
        "io",
        "re",
        "math",
        "time",
        "random",
        "pathlib",
        "typing",
        "abc",
        "enum",
        "copy",
        "warnings",
        # Data formats
        "json",
        "csv",
        "pickle",
        "struct",
        "base64",
        # Datetime
        "datetime",
        "calendar",
        "zoneinfo",
        # Collections and data structures
        "collections",
        "itertools",
        "functools",
        "operator",
        "dataclasses",
        "heapq",
        "bisect",
        # Logging and debugging
        "logging",
        "traceback",
        "pdb",
        "inspect",
        # Security and hashing
        "hashlib",
        "hmac",
        "secrets",
        # Concurrency
        "threading",
        "multiprocessing",
        "concurrent",
        "asyncio",
        "queue",
        # Networking
        "socket",
        "http",
        "urllib",
        "ssl",
        # File and archive
        "shutil",
        "glob",
        "tempfile",
        "gzip",
        "zipfile",
        "tarfile",
        # Testing
        "unittest",
        "doctest",
        # Other common
        "string",
        "textwrap",
        "difflib",
        "decimal",
        "fractions",
        "statistics",
        "contextlib",
        "weakref",
        "types",
        "uuid",
        "platform",
        "argparse",
        "getpass",
        "configparser",
    }

    def visit_Import(self, node: ast.Import) -> None:
        """
        Track `import X` and `import X as Y` statements.

        Examples:
            import pyspark.sql.functions as F
            import my_lib
        """
        for alias in node.names:
            module_name = alias.name
            module_alias = alias.asname  # None if no alias

            # Add or update the import entry
            if module_name not in self.imports:
                self.imports[module_name] = {
                    "alias": module_alias,
                    "imported_names": [],
                    "type": self._classify_import(module_name),
                    "has_source": self._check_source_available(module_name),
                }
            elif module_alias:
                # Update alias if provided
                self.imports[module_name]["alias"] = module_alias

        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """
        Track `from X import Y` and `from X import Y as Z` statements.

        Examples:
            from pyspark.sql import functions as F
                -> pyspark.sql.functions with alias="F"
            from pyspark.sql.functions import col, lit, when
                -> pyspark.sql.functions with imported_names=["col", "lit", "when"]
            from pyspark.sql.functions import sum as _sum
                -> pyspark.sql.functions with imported_names=["sum"] and name_aliases={"sum": "_sum"}
        """
        base_module = node.module or ""

        for alias in node.names:
            if alias.name == "*":
                # Handle `from X import *`
                if base_module not in self.imports:
                    self.imports[base_module] = {
                        "alias": None,
                        "imported_names": ["*"],
                        "type": self._classify_import(base_module),
                        "has_source": self._check_source_available(base_module),
                    }
                elif "*" not in self.imports[base_module]["imported_names"]:
                    self.imports[base_module]["imported_names"].append("*")

            elif self._is_submodule_import(base_module, alias.name):
                # Case: `from pyspark.sql import functions as F`
                # This imports a submodule, not a class/function
                full_module = f"{base_module}.{alias.name}" if base_module else alias.name

                if full_module not in self.imports:
                    self.imports[full_module] = {
                        "alias": alias.asname,  # "F" or None
                        "imported_names": [],
                        "type": self._classify_import(full_module),
                        "has_source": self._check_source_available(full_module),
                    }
                elif alias.asname:
                    # Update alias if provided
                    self.imports[full_module]["alias"] = alias.asname

            else:
                # Case: `from X import Y` or `from X import Y as Z`
                # This imports a class/function
                if base_module not in self.imports:
                    self.imports[base_module] = {
                        "alias": None,
                        "imported_names": [],
                        "type": self._classify_import(base_module),
                        "has_source": self._check_source_available(base_module),
                    }

                # Add the imported name (with optional alias tracking)
                if alias.asname:
                    # Store as "original_name:alias" for functions that have aliases
                    # e.g., "sum:_sum" means `sum` is aliased as `_sum`
                    name_entry = f"{alias.name}:{alias.asname}"
                else:
                    name_entry = alias.name

                if name_entry not in self.imports[base_module]["imported_names"]:
                    self.imports[base_module]["imported_names"].append(name_entry)

        self.generic_visit(node)

    def _is_submodule_import(self, base_module: str, name: str) -> bool:
        """
        Check if `name` is a submodule of `base_module`.

        Only returns True for KNOWN submodules to avoid incorrectly
        treating functions like `col`, `sum` as submodules.

        Example:
            _is_submodule_import("pyspark.sql", "functions") -> True
            _is_submodule_import("pyspark.sql", "SparkSession") -> False
            _is_submodule_import("pyspark.sql.functions", "col") -> False
        """
        # Only check against known submodules - no heuristics
        if base_module in self._KNOWN_SUBMODULES:
            return name in self._KNOWN_SUBMODULES[base_module]

        return False

    def _classify_import(self, module: str) -> str:
        """Classify a module as external, custom, or local."""
        # Check PySpark
        for known in self._PYSPARK_MODULES:
            if module == known or module.startswith(f"{known}."):
                return "external_library"

        # Check stdlib
        root_module = module.split(".")[0]
        if root_module in self._STDLIB_MODULES:
            return "external_library"

        # Relative import
        if module.startswith("."):
            return "local_module"

        return "custom_library"

    def _check_source_available(self, module: str) -> bool:
        """
        Check if source code for a module is available in the workload.

        For custom_library imports, checks if the module exists as:
        - {module}.py file
        - {module}/__init__.py (package)
        - Nested path from module name (e.g., my.lib -> my/lib.py)

        Args:
            module: The module name (e.g., "my_security_lib", "utils.helpers")

        Returns:
            True if source is available, False otherwise
        """
        # External libraries and stdlib don't need source check
        import_type = self._classify_import(module)
        if import_type == "external_library":
            return True  # Known library, source not needed

        # No workload root set - can't determine
        if not hasattr(self, "_workload_root") or self._workload_root is None:
            return True  # Assume available if we can't check

        root = self._workload_root

        # Convert module name to possible file paths
        # e.g., "my_security_lib" -> ["my_security_lib.py", "my_security_lib/__init__.py"]
        # e.g., "utils.helpers" -> ["utils/helpers.py", "utils/helpers/__init__.py"]
        module_path = module.replace(".", "/")

        possible_paths = [
            root / f"{module_path}.py",
            root / module_path / "__init__.py",
        ]

        # Also check if any .py file with the module name exists (recursive)
        # This handles cases like subfolder/my_module.py
        module_base = module.split(".")[-1]

        for path in possible_paths:
            if path.exists():
                return True

        # Recursive search for the module file in subdirectories
        for py_file in root.rglob(f"{module_base}.py"):
            return True

        return False
