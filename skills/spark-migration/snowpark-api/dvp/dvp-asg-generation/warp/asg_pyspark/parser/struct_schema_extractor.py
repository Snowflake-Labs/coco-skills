"""PySpark StructType schema extractor.

Scans Python source files for module-level StructType / StructField definitions
and builds a cross-file schema registry.  A subsequent linker pass assigns the
discovered schemas to DataSource nodes whose ``createDataFrame`` or
``read.csv/parquet`` calls reference a known schema variable, replacing garbage
join-condition columns with authoritative SCHEMA_DEFINITION columns.

Supported patterns
------------------
Pattern A — direct inline StructType:
    MY_SCHEMA = StructType([
        StructField("col1", StringType(), True),
        StructField("col2", IntegerType(), False),
    ])

Pattern B — two-step list + StructType (SocGen MRB style):
    my_list = [
        StructField("col1", StringType(), True),
        StructField("col2", IntegerType(), False),
    ]
    MY_SCHEMA = StructType(fields=my_list)

Pattern C — mixed (list of StructFields used directly in createDataFrame):
    spark.createDataFrame(data, my_list)

Linkage
-------
``createDataFrame(data, schema_ref)`` calls are detected and matched to a known
schema variable, taking into account import aliases (``import schemaMBR as
schema`` → ``schema.MY_SCHEMA``).

Type Mapping
------------
PySpark types are normalised to the WARP internal type vocabulary.
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from warp_core.ir.pyspark_models import ASG

# ---------------------------------------------------------------------------
# PySpark → WARP type map
# ---------------------------------------------------------------------------

_PYSPARK_TO_WARP: dict[str, str] = {
    "StringType": "STRING",
    "VarcharType": "STRING",
    "CharType": "STRING",
    "IntegerType": "INTEGER",
    "LongType": "INTEGER",
    "ShortType": "INTEGER",
    "ByteType": "INTEGER",
    "DoubleType": "DOUBLE",
    "FloatType": "DOUBLE",
    "DecimalType": "DOUBLE",
    "TimestampType": "DATETIME",
    "DateType": "DATETIME",
    "BooleanType": "BOOLEAN",
    "BinaryType": "STRING",
    "ArrayType": "STRING",
    "MapType": "STRING",
    "StructType": "STRING",
}

# Heuristic: column names containing these substrings are garbage from join
# condition inference and should be evicted when a real schema is available.
_GARBAGE_COLUMN_RE = re.compile(
    r"col\(|==|!=|>=|<=|&|\||\bAND\b|\bOR\b",
    re.IGNORECASE,
)


def _col_name_is_garbage(name: str) -> bool:
    return len(name) > 60 or bool(_GARBAGE_COLUMN_RE.search(name))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_func_name(node: ast.expr) -> str | None:
    """Return the base function name from a Call node's func attribute."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _extract_structfield(call: ast.Call) -> tuple[str, str] | None:
    """Extract (column_name, warp_type) from a StructField(...) Call node.

    Accepts both positional and keyword argument forms:
        StructField("name", StringType(), True)
        StructField(name="name", dataType=StringType(), nullable=True)
    """
    # Resolve name
    col_name: str | None = None
    type_name: str | None = None

    # Positional args
    if call.args:
        arg0 = call.args[0]
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
            col_name = arg0.value
        if len(call.args) >= 2:
            type_node = call.args[1]
            if isinstance(type_node, ast.Call):
                type_name = _get_func_name(type_node.func)

    # Keyword args (may override positional)
    for kw in call.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
            col_name = str(kw.value.value)
        elif kw.arg == "dataType" and isinstance(kw.value, ast.Call):
            type_name = _get_func_name(kw.value.func)

    if col_name is None:
        return None
    warp_type = _PYSPARK_TO_WARP.get(type_name or "", "UNKNOWN")
    return col_name, warp_type


def _extract_fields_from_list(elts: list[ast.expr]) -> list[tuple[str, str]]:
    """Extract (name, type) pairs from a list of StructField call nodes."""
    fields: list[tuple[str, str]] = []
    for elt in elts:
        if not isinstance(elt, ast.Call):
            continue
        func_name = _get_func_name(elt.func)
        if func_name != "StructField":
            continue
        result = _extract_structfield(elt)
        if result:
            fields.append(result)
    return fields


# ---------------------------------------------------------------------------
# Schema Registry
# ---------------------------------------------------------------------------

class SchemaRegistry:
    """Cross-file registry mapping variable names to column definitions.

    Keys are (file_path, var_name) for file-local lookups, plus bare
    var_name for global fallback when the same name appears in only one file.
    """

    def __init__(self) -> None:
        # (file_path, var_name) → list[(col_name, warp_type)]
        self._schemas: dict[tuple[str, str], list[tuple[str, str]]] = {}
        # var_name → count of files defining it (for ambiguity detection)
        self._global_count: dict[str, int] = {}

    def register(
        self,
        file_path: str,
        var_name: str,
        fields: list[tuple[str, str]],
    ) -> None:
        if not fields:
            return
        key = (file_path, var_name)
        self._schemas[key] = fields
        self._global_count[var_name] = self._global_count.get(var_name, 0) + 1

    def lookup(
        self,
        var_name: str,
        file_path: str | None = None,
    ) -> list[tuple[str, str]] | None:
        """Return fields for *var_name*, preferring *file_path* scope."""
        if file_path:
            result = self._schemas.get((file_path, var_name))
            if result:
                return result
        # Global fallback: only if unambiguous (defined in exactly one file)
        if self._global_count.get(var_name, 0) == 1:
            for (fp, vn), fields in self._schemas.items():
                if vn == var_name:
                    return fields
        return None

    def all_var_names(self) -> set[str]:
        return {vn for _, vn in self._schemas}

    def files_for_module(self, module_basename: str) -> list[str]:
        """Return all file paths whose basename matches *module_basename*.

        Used to resolve import aliases: ``import shared.schemaMBR as schema``
        → ``module_basename = "schemaMBR"`` → returns the schemaMBR.py paths.
        """
        import os
        result = []
        seen_fps = {fp for fp, _ in self._schemas}
        for fp in seen_fps:
            base = os.path.splitext(os.path.basename(fp))[0]
            if base == module_basename:
                result.append(fp)
        return result


# ---------------------------------------------------------------------------
# Per-file extractor
# ---------------------------------------------------------------------------

class _FileSchemaExtractor(ast.NodeVisitor):
    """Walk a single module's AST and extract StructType schema definitions."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        # var_name → list[(col_name, warp_type)]  (intermediate list vars)
        self._field_lists: dict[str, list[tuple[str, str]]] = {}
        # var_name → list[(col_name, warp_type)]  (finalised StructType vars)
        self._struct_schemas: dict[str, list[tuple[str, str]]] = {}

    def visit_Assign(self, node: ast.Assign) -> None:
        """Handle module-level assignments only (not inside functions)."""
        # Only process simple name assignments at module level
        if not node.targets or not isinstance(node.targets[0], ast.Name):
            self.generic_visit(node)
            return

        var_name: str = node.targets[0].id
        value = node.value

        # Pattern A & C: var = [StructField(...), ...]
        if isinstance(value, ast.List):
            fields = _extract_fields_from_list(value.elts)
            if fields:
                self._field_lists[var_name] = fields

        # Pattern A: var = StructType([StructField(...), ...])
        # Pattern B: var = StructType(fields=other_var)
        elif isinstance(value, ast.Call):
            func_name = _get_func_name(value.func)
            if func_name == "StructType":
                fields = self._resolve_structtype_call(value)
                if fields:
                    self._struct_schemas[var_name] = fields

        self.generic_visit(node)

    def _resolve_structtype_call(
        self,
        call: ast.Call,
    ) -> list[tuple[str, str]]:
        """Resolve fields from a StructType(...) call node."""
        # StructType([StructField(...), ...])  — first positional arg is a list
        if call.args and isinstance(call.args[0], ast.List):
            return _extract_fields_from_list(call.args[0].elts)

        # StructType(fields=var_name)  — keyword arg referencing a list var
        for kw in call.keywords:
            if kw.arg == "fields":
                if isinstance(kw.value, ast.Name):
                    ref = kw.value.id
                    # Try to resolve from already-seen list assignments
                    if ref in self._field_lists:
                        return self._field_lists[ref]
                elif isinstance(kw.value, ast.List):
                    return _extract_fields_from_list(kw.value.elts)

        return []

    def extract(self) -> dict[str, list[tuple[str, str]]]:
        """Return all discovered schemas (both list-style and StructType)."""
        result: dict[str, list[tuple[str, str]]] = {}
        result.update(self._field_lists)
        result.update(self._struct_schemas)
        return result


# ---------------------------------------------------------------------------
# Public API: build registry from file_asts
# ---------------------------------------------------------------------------

def build_schema_registry(
    file_asts: list[tuple[str, str]],
) -> SchemaRegistry:
    """Scan all files and return a populated :class:`SchemaRegistry`.

    Args:
        file_asts: list of (relative_path, source_code) as provided by the
            PySpark parser's ``_run_post_merge_phases``.
    """
    registry = SchemaRegistry()

    for file_path, source_code in file_asts:
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            continue

        extractor = _FileSchemaExtractor(file_path)
        extractor.visit(tree)

        for var_name, fields in extractor.extract().items():
            registry.register(file_path, var_name, fields)

    return registry


# ---------------------------------------------------------------------------
# Import alias resolver
# ---------------------------------------------------------------------------

def _build_import_aliases(source_code: str) -> dict[str, str]:
    """Return a map of {alias: module_basename} from import statements.

    E.g. ``import shared.schemaMBR as schema`` → ``{"schema": "schemaMBR"}``.
    Only tracks aliases that look like schema modules (contain "schema" or
    "Schema" in the module name — conservative heuristic).
    """
    aliases: dict[str, str] = {}
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return aliases

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod_base = alias.name.split(".")[-1]
                local = alias.asname or mod_base
                aliases[local] = mod_base
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            mod_base = mod.split(".")[-1]
            for alias in node.names:
                local = alias.asname or alias.name
                aliases[local] = mod_base

    return aliases


def _resolve_schema_var(
    arg: ast.expr,
    import_aliases: dict[str, str],
    registry: SchemaRegistry,
    file_path: str,
) -> list[tuple[str, str]] | None:
    """Resolve a schema argument (Name or Attribute) to fields in registry.

    Handles:
    - ``my_schema``           → direct lookup in current file, then global
    - ``schema.MY_STRUCT``    → resolve alias to module file, then file-local lookup
    """
    if isinstance(arg, ast.Name):
        return registry.lookup(arg.id, file_path)

    if isinstance(arg, ast.Attribute):
        attr_name = arg.attr
        # Determine the module alias (e.g. `schema` in `schema.MY_STRUCT`)
        alias_name: str | None = None
        if isinstance(arg.value, ast.Name):
            alias_name = arg.value.id

        # Resolve alias → module basename → candidate files
        if alias_name and alias_name in import_aliases:
            module_base = import_aliases[alias_name]
            candidate_files = registry.files_for_module(module_base)
            for candidate_fp in candidate_files:
                result = registry.lookup(attr_name, candidate_fp)
                if result:
                    return result

        # Fallback: try current file, then global (unambiguous)
        result = registry.lookup(attr_name, file_path)
        if result:
            return result
        return registry.lookup(attr_name)

    return None


# ---------------------------------------------------------------------------
# Linker: assign discovered schemas to DataSource nodes
# ---------------------------------------------------------------------------

def link_schemas_to_data_sources(
    asg: "ASG",
    file_asts: list[tuple[str, str]],
    registry: SchemaRegistry,
) -> None:
    """Assign StructType-derived schemas to DataSource nodes.

    For each DataSource (data_in), the linker:
    1. Looks at the source file for ``createDataFrame(data, schema_ref)`` calls.
    2. Resolves ``schema_ref`` against the registry (respecting import aliases).
    3. Assigns the resolved columns with ``SCHEMA_DEFINITION`` / ``HIGH``.
    4. Purges garbage join-condition column names when a real schema is set.
    """
    from warp_core.ir.pyspark_models import (
        InferredColumn,
        InferenceSource,
        InferenceConfidence,
    )

    # Build per-file source index for quick lookup
    file_source: dict[str, str] = dict(file_asts)

    # Index data_in by source file
    ds_by_file: dict[str, list] = {}
    for ds in asg.data_in:
        pathfile = (ds.location.pathfile or "") if ds.location else ""
        ds_by_file.setdefault(pathfile, []).append(ds)

    for file_path, source_code in file_asts:
        data_sources = ds_by_file.get(file_path, [])
        if not data_sources:
            continue

        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            continue

        import_aliases = _build_import_aliases(source_code)

        # Find createDataFrame(data, schema) calls and the variable they assign to
        # Also find spark.read.csv(path, schema=schema_var) patterns
        schema_by_varname: dict[str, list[tuple[str, str]]] = {}

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue

            call = node.value
            func_name = _get_func_name(call.func)

            fields: list[tuple[str, str]] | None = None

            # createDataFrame(data, schema)
            if func_name == "createDataFrame":
                schema_arg: ast.expr | None = None
                if len(call.args) >= 2:
                    schema_arg = call.args[1]
                else:
                    for kw in call.keywords:
                        if kw.arg == "schema":
                            schema_arg = kw.value
                            break
                if schema_arg is not None:
                    fields = _resolve_schema_var(
                        schema_arg, import_aliases, registry, file_path
                    )

            # spark.read.csv/parquet/json(path, schema=schema_var)
            elif func_name in {"csv", "parquet", "json", "orc", "load"}:
                for kw in call.keywords:
                    if kw.arg == "schema":
                        fields = _resolve_schema_var(
                            kw.value, import_aliases, registry, file_path
                        )
                        break

            if fields and node.targets and isinstance(node.targets[0], ast.Name):
                assigned_var = node.targets[0].id
                schema_by_varname[assigned_var] = fields

        # Match the discovered schema vars to DataSource nodes
        for ds in data_sources:
            ds_name = ds.name or ""
            # DataSource names from memory nodes are "df:varname"
            var_part = ds_name.split(":")[-1] if ":" in ds_name else ds_name
            if var_part in schema_by_varname:
                _apply_schema_to_datasource(
                    ds,
                    schema_by_varname[var_part],
                    InferredColumn,
                    InferenceSource,
                    InferenceConfidence,
                )


def _apply_schema_to_datasource(
    ds: object,
    fields: list[tuple[str, str]],
    InferredColumn: type,
    InferenceSource: type,
    InferenceConfidence: type,
) -> None:
    """Replace or set inferred_columns on *ds* with *fields* at SCHEMA_DEFINITION quality.

    Existing SCHEMA_DEFINITION columns are preserved; garbage columns (join
    conditions) are evicted; new columns are appended if not already present.
    """
    if not fields:
        return

    new_cols = [
        InferredColumn(
            name=name,
            inferred_type=warp_type,
            source=InferenceSource.SCHEMA_DEFINITION,
            confidence=InferenceConfidence.HIGH,
        )
        for name, warp_type in fields
    ]

    existing = ds.inferred_columns or []

    # Keep existing SCHEMA_DEFINITION cols; drop garbage cols
    kept = [
        c for c in existing
        if (
            getattr(c, "source", None) == InferenceSource.SCHEMA_DEFINITION
            and not _col_name_is_garbage(c.name or "")
        )
    ]

    # Merge: SCHEMA_DEFINITION wins; don't duplicate names already in kept
    kept_names = {c.name for c in kept}
    new_schema_names = {c.name for c in new_cols}

    # Drop garbage from existing non-schema cols if new schema is available
    non_schema_clean = [
        c for c in existing
        if (
            getattr(c, "source", None) != InferenceSource.SCHEMA_DEFINITION
            and not _col_name_is_garbage(c.name or "")
            and (c.name or "") not in new_schema_names
        )
    ]

    ds.inferred_columns = new_cols + [
        c for c in non_schema_clean if (c.name or "") not in new_schema_names
    ]


# ---------------------------------------------------------------------------
# Garbage sanitizer (standalone pass)
# ---------------------------------------------------------------------------

def sanitize_garbage_columns(asg: "ASG") -> None:
    """Remove join-condition garbage from DataSource nodes that have a real schema.

    A node is considered to have a real schema when at least one column carries
    ``InferenceSource.SCHEMA_DEFINITION``.  In that case, all columns whose
    name matches ``_GARBAGE_COLUMN_RE`` or exceeds 60 chars are dropped.
    """
    from warp_core.ir.pyspark_models import InferenceSource

    for ds in asg.data_in:
        cols = ds.inferred_columns or []
        if not cols:
            continue

        has_schema_def = any(
            getattr(c, "source", None) == InferenceSource.SCHEMA_DEFINITION
            for c in cols
        )
        if not has_schema_def:
            continue

        ds.inferred_columns = [
            c for c in cols if not _col_name_is_garbage(c.name or "")
        ]


# ---------------------------------------------------------------------------
# Entry point for _run_post_merge_phases
# ---------------------------------------------------------------------------

def apply_struct_schemas(
    asg: "ASG",
    file_asts: list[tuple[str, str]],
) -> None:
    """Full pipeline: build registry → link → sanitize.

    Call this from ``_run_post_merge_phases`` after ``_propagate_columns_by_name``
    so that name-based xref has already run.
    """
    registry = build_schema_registry(file_asts)
    link_schemas_to_data_sources(asg, file_asts, registry)
    sanitize_garbage_columns(asg)
