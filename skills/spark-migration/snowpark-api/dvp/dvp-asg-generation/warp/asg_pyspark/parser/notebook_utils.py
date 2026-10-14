"""
Databricks Notebook Detection, Preprocessing, and Metadata Extraction.

Standalone utilities that operate on source code strings — no dependency on
SparkASTParser or the ASG model.
"""

from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath


DBX_NOTEBOOK_HEADER = "# Databricks notebook source"


# =============================================================================
# Detection helpers
# =============================================================================

def is_databricks_notebook(source_code: str) -> bool:
    """Check if source code is from a Databricks notebook export."""
    return source_code.strip().startswith(DBX_NOTEBOOK_HEADER)


def has_spark_session_creation(source_code: str) -> bool:
    """Check if source code creates a SparkSession (typically an entry point)."""
    patterns = [
        "SparkSession.builder",
        ".getOrCreate()",
        "getActiveSession()",
    ]
    return any(pattern in source_code for pattern in patterns)


def has_main_guard(source_code: str) -> bool:
    """Check if source code has if __name__ == '__main__' guard."""
    return '__name__' in source_code and '__main__' in source_code


def find_main_guard_lineno(source_code: str) -> int | None:
    """Return the line number of the ``if __name__ == '__main__':`` guard via AST.

    Returns None if the guard is not present or the source cannot be parsed.
    Falls back to a text scan if the AST parse fails (e.g. Python 2 syntax).
    """
    import ast as _ast
    try:
        tree = _ast.parse(source_code)
    except SyntaxError:
        # Fallback: text scan for the pattern
        for i, line in enumerate(source_code.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("if") and "__name__" in stripped and "__main__" in stripped:
                return i
        return None

    for node in _ast.walk(tree):
        if not isinstance(node, _ast.If):
            continue
        test = node.test
        # Match: __name__ == '__main__'  or  '__main__' == __name__
        if isinstance(test, _ast.Compare):
            left = test.left
            ops = test.ops
            comps = test.comparators
            if (
                isinstance(ops[0], _ast.Eq)
                and (
                    (isinstance(left, _ast.Name) and left.id == "__name__"
                     and isinstance(comps[0], _ast.Constant) and comps[0].value == "__main__")
                    or
                    (isinstance(left, _ast.Constant) and left.value == "__main__"
                     and isinstance(comps[0], _ast.Name) and comps[0].id == "__name__")
                )
            ):
                return node.lineno
    return None


def detect_source_type(source_code: str, is_notebook: bool) -> str:
    """Detect the type of source file."""
    if is_notebook:
        return "notebook"
    if has_main_guard(source_code) or has_spark_session_creation(source_code):
        return "script"
    return "module"


def is_entry_point(source_code: str, is_notebook: bool) -> bool:
    """Determine if this file is an execution entry point."""
    return detect_entry_point_reason(source_code, is_notebook) is not None


def detect_entry_point_reason(source_code: str, is_notebook: bool) -> str | None:
    """Return the reason this file is an entry point, or None if it is not.

    Possible return values:
    - "notebook"               — Databricks notebook (always an entry point)
    - "main_guard"             — has ``if __name__ == '__main__':`` guard
    - "spark_session_creation" — creates a SparkSession directly
    """
    if is_notebook:
        return "notebook"
    if has_main_guard(source_code):
        return "main_guard"
    if has_spark_session_creation(source_code):
        return "spark_session_creation"
    return None


# =============================================================================
# Preprocessing
# =============================================================================

# Regex that handles lambda tuple unpacking up to 2 levels of nesting:
#   lambda (a, b):              → level 0 (simple)
#   lambda (a, (b, c)):         → level 1
#   lambda (a, (b, (c, d))):    → level 2
_LAMBDA_NESTED = r'(?:[^()]*|\((?:[^()]*|\([^()]*\))*\))*'
_LAMBDA_PY2_RE = re.compile(rf'lambda\s*\({_LAMBDA_NESTED}\)\s*:')

# Matches Python 2 `print expr` statements.
# Negative lookahead (?!\() avoids touching already-valid print(...) calls.
# Also matches print'string' and print"string" (no space before quote).
_PRINT_PY2_RE = re.compile(
    r'^(\s*)print\s*(?!\()(.+?)(\s*(?:#.*)?)$',
    re.MULTILINE,
)

# Joins backslash line-continuations into a single logical line so that
# multi-line print arguments become one line before the print transform.
_BACKSLASH_CONT_RE = re.compile(r'[ \t]*\\\n[ \t]*')


def _apply_python2_compat(source_code: str) -> tuple[str, list[str]]:
    """Apply lightweight Python 2 → Python 3 syntax transformations.

    The transforms are applied in-memory only — source files on disk are
    never modified.  Semantics are preserved well enough for static data-flow
    analysis; the resulting code is **not** intended to be executed.

    Transforms applied (in order):
    1. Strip UTF-8 BOM (``U+FEFF``) — common in Windows-saved files.
    2. Join backslash line-continuations — collapses ``expr \\\\\\n  cont``
       into a single line so the print transform works on multi-line stmts.
    3. ``print x`` → ``print(x)`` — Python 2 print statement.
    4. ``lambda (a, (b, c)):`` → ``lambda *__py2_args:`` — Python 2 tuple
       parameter unpacking (up to two levels of nesting).

    Returns:
        Tuple of (transformed_code, list[str] describing each correction).
    """
    corrections: list[str] = []
    code = source_code

    # 1. BOM
    if code.startswith('\ufeff'):
        code = code[1:]
        corrections.append("stripped UTF-8 BOM (U+FEFF)")

    # 2. Join backslash continuations (must precede print transform so that
    #    multi-line ``print "a" \\\n        "b"`` becomes one line)
    new_code = _BACKSLASH_CONT_RE.sub(' ', code)
    if new_code != code:
        corrections.append("backslash line-continuations joined")
        code = new_code

    # 3. print statement → print() call
    new_code = _PRINT_PY2_RE.sub(
        lambda m: f'{m.group(1)}print({m.group(2).rstrip()}){m.group(3)}',
        code,
    )
    if new_code != code:
        corrections.append("Python 2 print statements wrapped in print()")
        code = new_code

    # 4. Lambda tuple-parameter unpacking
    new_code = _LAMBDA_PY2_RE.sub('lambda *__py2_args:', code)
    if new_code != code:
        corrections.append("Python 2 lambda tuple-unpacking neutralized")
        code = new_code

    return code, corrections


def fix_indentation_errors(source_code: str) -> tuple[str, list[str]]:
    """
    Attempt to fix common indentation errors in source code.

    Returns:
        Tuple of (fixed_code, list of corrections applied)
    """
    corrections: list[str] = []
    lines = source_code.split('\n')
    fixed_lines = []

    for i, line in enumerate(lines):
        line_num = i + 1
        stripped = line.lstrip()

        if not stripped or stripped.startswith('#'):
            fixed_lines.append(line)
            continue

        current_indent = len(line) - len(stripped)

        if current_indent > 0 and current_indent <= 3:
            if (stripped.startswith(('def ', 'class ', 'import ', 'from ')) or
                ('=' in stripped and not stripped.startswith(('if ', 'elif ', 'while ', 'for ')))):
                fixed_lines.append(stripped)
                corrections.append(f"Removed {current_indent} leading space(s) at line {line_num}")
                continue

        fixed_lines.append(line)

    return '\n'.join(fixed_lines), corrections


def preprocess_source(source_code: str, file_path: str) -> tuple[str, str, list[str]]:
    """Preprocess source code, detecting file type and fixing errors if needed.

    Applies fixes in order, stopping at the first version that parses cleanly:

    1. Raw source — no changes needed (fast path).
    2. Indentation fix — corrects common off-by-one indentation issues.
    3. Python 2 compat shim — handles ``print x``, BOM, and lambda tuple
       unpacking so that Python 2 codebases can be analysed without
       modifying the files on disk.

    Returns:
        Tuple of (processed_code, file_type, corrections)
        file_type is "databricks_notebook" or "python_script"
    """
    file_type = "databricks_notebook" if is_databricks_notebook(source_code) else "python_script"

    # Fast path: parses as-is
    try:
        ast.parse(source_code)
        return source_code, file_type, []
    except (SyntaxError, IndentationError):
        pass

    # Attempt 2: indentation fix
    fixed_code, corrections = fix_indentation_errors(source_code)
    if corrections:
        try:
            ast.parse(fixed_code)
            return fixed_code, file_type, corrections
        except (SyntaxError, IndentationError):
            pass

    # Attempt 3: Python 2 compat shim (BOM + print + lambda)
    py2_code, py2_corrections = _apply_python2_compat(source_code)
    if py2_corrections:
        try:
            ast.parse(py2_code)
            return py2_code, file_type, py2_corrections
        except (SyntaxError, IndentationError):
            pass

    return source_code, file_type, []


# =============================================================================
# Metadata extraction
# =============================================================================

def extract_notebook_dependencies(source_code: str, file_path: str) -> list[dict]:
    """
    Extract %run and dbutils.notebook.run dependencies from Databricks notebook source.

    Uses string parsing for %run (comments, invisible to AST) and AST for
    dbutils.notebook.run (valid Python code).

    Returns list of dicts with target, params, line, resolved_path.
    """
    dependencies: list[dict] = []
    seen: set[tuple] = set()

    # Part 1: Extract %run from MAGIC comments (not visible to Python AST)
    for lineno, line in enumerate(source_code.splitlines(), 1):
        stripped = line.strip()

        if stripped.startswith("# MAGIC %run "):
            rest = stripped[len("# MAGIC %run "):].strip()
            parts = rest.split(" $")
            target = parts[0].strip()

            params: dict[str, str] = {}
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    params[k.strip()] = v.strip().strip('"\'')

            dep_key = (target, tuple(sorted(params.items())))
            if dep_key not in seen:
                seen.add(dep_key)
                resolved = _resolve_notebook_path(target, file_path)
                dependencies.append({
                    "target": target,
                    "resolved_path": resolved,
                    "params": params,
                    "line": lineno,
                })

    # Part 2: Extract dbutils.notebook.run via AST (proper Python code)
    try:
        tree = ast.parse(source_code)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "run"):
                continue
            val = func.value
            if not (isinstance(val, ast.Attribute) and val.attr == "notebook"
                    and isinstance(val.value, ast.Name) and val.value.id == "dbutils"):
                continue

            if not node.args:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                target = first_arg.value
            else:
                target = "runtime:" + ast.unparse(first_arg)

            params = {}
            if len(node.args) >= 3:
                third_arg = node.args[2]
                if isinstance(third_arg, ast.Dict):
                    for k, v in zip(third_arg.keys, third_arg.values):
                        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                            params[str(k.value)] = str(v.value)

            dep_key = (target, tuple(sorted(params.items())))
            if dep_key not in seen:
                seen.add(dep_key)
                resolved = _resolve_notebook_path(target, file_path) if not target.startswith("runtime:") else None
                dependencies.append({
                    "target": target,
                    "resolved_path": resolved,
                    "params": params,
                    "line": getattr(node, "lineno", None),
                })
    except SyntaxError:
        pass

    return dependencies


def extract_udf_definitions(source_code: str) -> list[dict]:
    """
    Extract UDF definitions via AST.

    Detects:
        udf(func, return_type)
        spark.udf.register(name, func, return_type)
        @udf(returnType=...)

    Returns list of dicts with function_name, return_schema, line.
    """
    udfs: list[dict] = []

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "udf":
                udf_info = _parse_udf_call(node)
                if udf_info:
                    udfs.append(udf_info)
            elif (isinstance(func, ast.Attribute) and func.attr == "register"
                  and isinstance(func.value, ast.Attribute) and func.value.attr == "udf"):
                udf_info = _parse_udf_register(node)
                if udf_info:
                    udfs.append(udf_info)

        elif isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    dec_func = decorator.func
                    if isinstance(dec_func, ast.Name) and dec_func.id == "udf":
                        schema = None
                        for kw in decorator.keywords:
                            if kw.arg == "returnType":
                                schema = ast.unparse(kw.value)
                        udfs.append({
                            "function_name": node.name,
                            "return_schema": schema,
                            "registered_name": None,
                            "line": node.lineno,
                        })
                elif isinstance(decorator, ast.Name) and decorator.id == "udf":
                    udfs.append({
                        "function_name": node.name,
                        "return_schema": None,
                        "registered_name": None,
                        "line": node.lineno,
                    })

    return udfs


def _parse_udf_call(node: ast.Call) -> dict | None:
    """Parse udf(func, return_type) call."""
    if not node.args:
        return None

    first = node.args[0]
    if isinstance(first, ast.Name):
        func_name = first.id
    else:
        func_name = ast.unparse(first)

    schema = None
    if len(node.args) >= 2:
        schema = ast.unparse(node.args[1])
    for kw in node.keywords:
        if kw.arg == "returnType":
            schema = ast.unparse(kw.value)

    return {
        "function_name": func_name,
        "return_schema": schema,
        "registered_name": None,
        "line": getattr(node, "lineno", None),
    }


def _parse_udf_register(node: ast.Call) -> dict | None:
    """Parse spark.udf.register(name, func, return_type) call."""
    if len(node.args) < 2:
        return None

    reg_name = None
    if isinstance(node.args[0], ast.Constant):
        reg_name = str(node.args[0].value)

    func_name = ast.unparse(node.args[1])

    schema = None
    if len(node.args) >= 3:
        schema = ast.unparse(node.args[2])
    for kw in node.keywords:
        if kw.arg == "returnType":
            schema = ast.unparse(kw.value)

    return {
        "function_name": func_name,
        "return_schema": schema,
        "registered_name": reg_name,
        "line": getattr(node, "lineno", None),
    }


def count_display_outputs(source_code: str) -> int:
    """Count display() and .show() calls via AST."""
    count = 0
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "display":
            count += 1
        elif isinstance(node.func, ast.Attribute) and node.func.attr in ("show", "display"):
            count += 1
    return count


def extract_notebook_description(source_code: str) -> str | None:
    """
    Extract notebook description from the first %md section.

    Looks for consecutive # MAGIC %md and # MAGIC lines that form the
    notebook's title/goal description.
    """
    lines = source_code.splitlines()
    md_lines: list[str] = []
    in_md_block = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("# MAGIC %md"):
            in_md_block = True
            rest = stripped[len("# MAGIC %md"):].strip()
            if rest:
                md_lines.append(rest)
        elif in_md_block and stripped.startswith("# MAGIC"):
            rest = stripped[len("# MAGIC"):].strip()
            if rest:
                md_lines.append(rest)
        elif in_md_block:
            break

    if not md_lines:
        return None

    cleaned = [ml.lstrip("#").strip() for ml in md_lines if ml.lstrip("#").strip()]
    return " — ".join(cleaned) if cleaned else None


def extract_widget_parameters(source_code: str) -> list[dict]:
    """
    Extract dbutils.widgets definitions via AST.

    Detects:
        dbutils.widgets.text(name, default, label)
        dbutils.widgets.dropdown(name, default, [choices], label)
        dbutils.widgets.combobox(name, default, [choices], label)
        dbutils.widgets.multiselect(name, default, [choices], label)
    """
    widgets: dict[str, dict] = {}

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        if not isinstance(func, ast.Attribute):
            continue

        method = func.attr
        if method not in ("text", "dropdown", "combobox", "multiselect", "get"):
            continue

        val = func.value
        if not (isinstance(val, ast.Attribute) and val.attr == "widgets"
                and isinstance(val.value, ast.Name) and val.value.id == "dbutils"):
            continue

        if not node.args:
            continue

        name_arg = node.args[0]
        if isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str):
            name = name_arg.value
        else:
            name = "runtime:" + ast.unparse(name_arg)

        if method == "get":
            if name not in widgets:
                widgets[name] = {
                    "name": name,
                    "widget_type": "text",
                    "default_value": None,
                    "valid_values": [],
                    "label": None,
                    "line": getattr(node, "lineno", None),
                }
            continue

        default_value = None
        if len(node.args) >= 2:
            dv = node.args[1]
            if isinstance(dv, ast.Constant):
                default_value = str(dv.value)
            else:
                default_value = "runtime:" + ast.unparse(dv)

        valid_values: list[str] = []
        if method in ("dropdown", "combobox", "multiselect") and len(node.args) >= 3:
            choices_arg = node.args[2]
            if isinstance(choices_arg, ast.List):
                for elt in choices_arg.elts:
                    if isinstance(elt, ast.Constant):
                        valid_values.append(str(elt.value))
                    else:
                        valid_values.append("runtime:" + ast.unparse(elt))

        label = None
        if method == "text" and len(node.args) >= 3:
            lbl = node.args[2]
            if isinstance(lbl, ast.Constant) and isinstance(lbl.value, str):
                label = lbl.value
        elif method in ("dropdown", "combobox", "multiselect") and len(node.args) >= 4:
            lbl = node.args[3]
            if isinstance(lbl, ast.Constant) and isinstance(lbl.value, str):
                label = lbl.value

        widgets[name] = {
            "name": name,
            "widget_type": method,
            "default_value": default_value,
            "valid_values": valid_values,
            "label": label,
            "line": getattr(node, "lineno", None),
        }

    return list(widgets.values())


def _resolve_notebook_path(target: str, file_path: str) -> str | None:
    """
    Resolve a relative notebook path to a path relative to workload root.

    Example: target="../config", file_path=".databricks/notebooks/rsuccess/plk/dim.py"
    -> ".databricks/notebooks/rsuccess/config.py"
    """
    file_dir = PurePosixPath(file_path).parent
    resolved = (file_dir / target).as_posix()

    parts = []
    for part in resolved.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)

    resolved = "/".join(parts)
    if not resolved.endswith(".py"):
        resolved += ".py"
    return resolved
