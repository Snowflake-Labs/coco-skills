"""notebook_source.py — stdlib-only Jupyter notebook to Python translator.

Translates .ipynb code cells into an executable Python source string suitable
for in-process ``compile`` + ``exec`` inside the validation harness. No third-
party dependencies (json, re, os only).

Public API:
  - ``to_python(path)`` — load a notebook file and return Python source.
  - ``notebook_dict_to_python(nb_dict)`` — translate from an in-memory dict.

Translation rules for cell magics:
  - ``%%sql`` / ``%sql`` body → ``spark.sql(...)`` per statement. Statements are
    split on ``;`` (quote- and comment-aware). The cell's result (last statement)
    is bound to Python variables so downstream cells can consume it:
      * ``_sqldf`` — always (Databricks' implicit last-SQL-cell binding).
      * ``<name>`` — additionally when the magic carries ``-r <name>`` /
        ``--result <name>`` (Snowflake Workspace's explicit per-cell binding).
    So ``%%sql -r my_result`` → ``my_result = _sqldf = spark.sql(...)`` and a later
    ``my_result[...]`` reference resolves.
  - ``%run <target>`` / ``dbutils.notebook.run(...)`` → ``_nb_run(...)`` call. When
    a ``dbutils.notebook.run`` return value is consumed (assignment/expression),
    a ``# NEEDS-REVIEW`` is emitted — ``_nb_run`` returns ``None`` whereas
    Databricks returns the child's exit string.
  - ``%python`` → strip magic line, keep body.
  - Other magics (``%pip``, ``%sh``, ``!cmd``, etc.) → ``pass  # notebook-magic``.
    Data-carrying magics also emit ``# NEEDS-REVIEW: <original>``.
"""

from __future__ import annotations

import json
import os
import re


# Magics that are purely side-effect-free / non-data-carrying.
_INERT_MAGICS = frozenset([
    "pip", "conda", "matplotlib", "time", "timeit", "load_ext",
    "env", "config", "who", "whos", "reset", "recall", "history",
    "lsmagic", "automagic", "pylab", "precision", "xdel",
    "doctest_mode", "pinfo", "pinfo2", "psource", "pdef", "pdoc",
    "pprint", "colors", "xmode", "quickref", "page", "logstart",
    "logstop", "logoff", "logon", "logstate", "macro", "save",
    "pastebin", "bookmark", "cd", "pwd", "pushd", "popd", "dirs",
    "dhist", "sc", "sx", "system", "alias", "unalias", "rehash",
    "rehashx", "store", "tb", "debug", "pdb", "profile", "prun",
    "gui", "notebook", "connect_info", "qtconsole", "autosave",
    "wildcard", "set_env", "capture", "html", "javascript", "js",
    "latex", "svg", "writefile", "edit", "less", "more", "man",
    "clear", "cls",
])

# Magics that potentially read external data (need review).
_DATA_MAGICS = frozenset(["fs", "sh", "bash"])

# Opener for a dbutils.notebook.run(...) call. The FULL call span (nested parens
# / multi-line args) is found by a balanced-paren scan in
# _translate_dbutils_in_python; the first string-literal arg is the target.
_DBUTILS_NB_RUN_OPEN = re.compile(r"dbutils\s*\.\s*notebook\s*\.\s*run\s*\(")
_STRING_ARG = re.compile(r"""["']([^"']+)["']""")


def _parse_run_target(rest: str) -> tuple[str, str]:
    """Extract the notebook path from a ``%run`` argument string.

    Returns ``(target, extra)`` where *extra* is any trailing text (e.g.
    Databricks widget args like ``$env="prod"``) that ``_nb_run`` cannot forward.
    The target is the first quoted string, or the first whitespace-delimited token.
    """
    rest = rest.strip()
    if rest[:1] in ("'", '"'):
        m = re.match(r"""(['"])(.*?)\1""", rest)
        if m:
            return m.group(2), rest[m.end():].strip()
        return rest.strip("'\""), ""
    parts = rest.split(None, 1)
    target = parts[0] if parts else ""
    extra = parts[1].strip() if len(parts) > 1 else ""
    return target, extra


def _first_arg(args_text: str) -> str:
    """Return the first top-level argument of a call's arg string (text up to the
    first top-level comma), respecting quotes, backslashes, and nested brackets."""
    depth = 0
    quote: str | None = None
    i = 0
    n = len(args_text)
    while i < n:
        ch = args_text[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            return args_text[:i]
        i += 1
    return args_text


def _escape_sql_for_triple_quote(sql: str) -> str:
    r"""Escape a SQL string so it can be safely placed inside triple double-quotes.

    Escaping every ``"`` as ``\"`` is value-preserving (``\"`` is just ``"`` at
    runtime) and makes the literal robust: no triple-double-quote sequence can
    form, and a statement ending in a double-quote can't merge with the closing
    delimiter to produce a syntax error (the earlier escape, which only rewrote
    triple-double-quote runs, left that gap).
    """
    return sql.replace("\\", "\\\\").replace('"', '\\"')


def _split_sql_statements(sql_body: str) -> list[str]:
    """Split a SQL body on statement-separating semicolons.

    A naive ``str.split(";")`` breaks on semicolons inside string literals
    (e.g. ``WHERE status = 'shipped; delivered'``), inside double-quoted
    identifiers, and inside ``--`` line / ``/* */`` block comments. This splitter
    ignores any ``;`` that falls inside a quoted span or a comment, and treats a
    doubled quote (``''`` / ``""``) as an escaped quote rather than a close.
    """
    stmts: list[str] = []
    current: list[str] = []
    quote: str | None = None  # open quote char, or None outside a literal
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(sql_body)
    while i < n:
        ch = sql_body[i]
        nxt = sql_body[i + 1] if i + 1 < n else ""

        if in_line_comment:
            current.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            current.append(ch)
            if ch == "*" and nxt == "/":
                current.append(nxt)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if quote is not None:  # inside a quoted literal/identifier
            if ch == "\\":  # backslash escapes the next char (Spark default)
                current.append(ch)
                if nxt:
                    current.append(nxt)
                i += 2
                continue
            if ch == quote:
                if nxt == quote:  # a doubled quote is an escaped quote, not a close
                    current.append(ch * 2)
                    i += 2
                    continue
                quote = None
            current.append(ch)
            i += 1
            continue

        # Outside quotes and comments.
        if ch == "-" and nxt == "-":
            in_line_comment = True
            current.append(ch)
            current.append(nxt)
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            current.append(ch)
            current.append(nxt)
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                stmts.append(stmt)
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1

    stmt = "".join(current).strip()
    if stmt:
        stmts.append(stmt)
    return stmts


# Result-binding flag on a SQL magic: ``-r <name>`` / ``--result <name>`` /
# ``--result=<name>`` (Snowflake Workspace explicit per-cell result variable).
_SQL_RESULT_FLAG = re.compile(r"(?:^|\s)(?:-r|--result)[=\s]+([A-Za-z_]\w*)")


def _parse_sql_result_var(flags: str) -> str | None:
    """Extract the explicit result-variable name from a SQL magic's flag string."""
    m = _SQL_RESULT_FLAG.search(flags or "")
    return m.group(1) if m else None


def _sql_to_spark_calls(sql_body: str, result_var: str | None = None) -> str:
    """Convert a SQL body (possibly multi-statement) to spark.sql() calls.

    Each statement becomes its own ``spark.sql(...)`` call — Snowpark Connect
    (SCOS) rejects multi-statement ``spark.sql()`` with ``PARSE_SYNTAX_ERROR``.
    The last statement's result is bound to ``_sqldf`` (always) and to
    ``result_var`` (when given) so downstream cells can reference it.
    """
    lines: list[str] = []
    if "{{" in sql_body or "${" in sql_body:
        lines.append(
            "# NEEDS-REVIEW: unresolved notebook parameter(s) ('{{...}}' / '${...}') "
            "in SQL — substitute a literal via the patch blueprint"
        )
    stmts = _split_sql_statements(sql_body)
    if not stmts:
        lines.append("pass  # notebook-magic: empty sql")
        return "\n".join(lines)

    # Every statement but the last runs for side effects; the last is captured.
    for stmt in stmts[:-1]:
        lines.append(f'spark.sql("""{_escape_sql_for_triple_quote(stmt)}""")')

    targets = ["_sqldf"]
    if result_var and result_var != "_sqldf":
        targets.insert(0, result_var)
    assign = " = ".join(targets)
    last = _escape_sql_for_triple_quote(stmts[-1])
    lines.append(f'{assign} = spark.sql("""{last}""")')
    return "\n".join(lines)


def _translate_cell(cell_source: str) -> str:
    """Translate a single code cell's source into Python.

    Returns the translated source string for this cell.
    """
    lines = cell_source.splitlines(keepends=True)
    if not lines:
        return ""

    first_line = lines[0].strip()

    # %%sql cell magic — entire cell body is SQL.
    # Handles %%sql, %%sql -d db, %%sql --database=mydb, %%sql -r <name>, etc.
    if first_line.lower() == "%%sql" or first_line.lower().startswith("%%sql "):
        result_var = _parse_sql_result_var(first_line[len("%%sql"):])
        sql_body = "".join(lines[1:])
        return _sql_to_spark_calls(sql_body, result_var)

    # %md / %skip (line- or cell-magic form) — a documentation or skipped cell.
    # Everything after is non-code (markdown / commented-out text), so neutralize
    # the WHOLE cell rather than leaking the body as bare Python. (Databricks .py
    # notebooks store these as a single-% "%md"/"%skip" magic.)
    if re.match(r"^%{1,2}(md|skip)\b", first_line, re.IGNORECASE):
        return "pass  # notebook-magic"

    # %%bash / %%sh cell magics — neutralize entire cell.
    if first_line.lower() in ("%%bash", "%%sh"):
        original = cell_source.strip()
        result = "pass  # notebook-magic"
        if any(kw in original.lower() for kw in ("aws s3", "gsutil", "az storage", "cp ", "mv ")):
            result += f"\n# NEEDS-REVIEW: {original.splitlines()[0]}"
        return result

    # Other %% cell magics — neutralize.
    if first_line.startswith("%%"):
        return "pass  # notebook-magic"

    # Process line-by-line for line magics.
    output_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # %sql line magic — rest of cell (or rest until next magic) is SQL.
        if re.match(r"^%sql\b", stripped, re.IGNORECASE):
            # The SQL is everything after %sql on this line plus remaining lines
            # until the cell ends or another magic appears.
            sql_on_line = re.sub(r"^%sql\s*", "", stripped, flags=re.IGNORECASE)
            # Parse a leading -r/--result <name> flag BEFORE deciding whether
            # same-line SQL exists — the query is often on the NEXT line (the loop
            # below slurps it), in which case the flag would otherwise be dropped.
            result_var = None
            mflag = re.match(r"^(?:-r|--result)[=\s]+([A-Za-z_]\w*)\s*(.*)$", sql_on_line, re.DOTALL)
            if mflag:
                result_var = mflag.group(1)
                sql_on_line = mflag.group(2)
            sql_parts = [sql_on_line]
            i += 1
            while i < len(lines):
                next_stripped = lines[i].strip()
                if next_stripped.startswith("%") or next_stripped.startswith("!"):
                    break
                sql_parts.append(lines[i].rstrip("\n"))
                i += 1
            sql_body = "\n".join(sql_parts)
            output_lines.append(_sql_to_spark_calls(sql_body, result_var))
            continue

        # %run line magic.
        run_match = re.match(r"^%run\s+(.+)$", stripped)
        if run_match:
            target, extra = _parse_run_target(run_match.group(1).strip())
            call = f'_nb_run("{target}", globals())'
            if extra:
                # Databricks %run passes widget args (e.g. `$env="prod"`) to the
                # child; _nb_run can't forward them — flag rather than emit broken
                # Python from the trailing tokens.
                call += f"  # NEEDS-REVIEW: %run args not forwarded: {extra}"
            output_lines.append(call)
            i += 1
            continue

        # %python — strip the magic line, rest is normal Python.
        if re.match(r"^%python\b", stripped, re.IGNORECASE):
            i += 1
            continue

        # Other line magics (%pip, %conda, %md, %fs, %sh, etc.).
        magic_match = re.match(r"^%(\w+)", stripped)
        if magic_match:
            magic_name = magic_match.group(1).lower()
            result = "pass  # notebook-magic"
            if magic_name in _DATA_MAGICS or (
                magic_name == "fs" and any(
                    kw in stripped.lower() for kw in ("cp", "ls", "head", "cat", "get", "put")
                )
            ):
                result += f"\n# NEEDS-REVIEW: {stripped}"
            output_lines.append(result)
            i += 1
            # %md / %skip neutralize the WHOLE remainder of the cell (markdown or
            # skipped code), not just their own line — otherwise the following lines
            # would survive as bare Python and fail to compile.
            if magic_name in ("md", "markdown", "skip"):
                while i < len(lines):
                    output_lines.append("# " + lines[i].rstrip("\n"))
                    i += 1
            continue

        # Shell escape (!cmd).
        if stripped.startswith("!"):
            result = "pass  # notebook-magic"
            if any(kw in stripped.lower() for kw in ("aws s3", "gsutil", "az storage", "wget", "curl")):
                result += f"\n# NEEDS-REVIEW: {stripped}"
            i += 1
            output_lines.append(result)
            continue

        # Normal Python line — keep as-is (preserve original indentation).
        output_lines.append(line.rstrip("\n"))
        i += 1

    return "\n".join(output_lines)


def _translate_dbutils_in_python(source: str) -> str:
    """Post-process: replace dbutils.notebook.run(...) calls with _nb_run(...).

    The full call span is found by a balanced-paren scan from the opening ``(``
    (respecting quotes and backslash escapes), so nested parens and multi-line
    args (e.g. ``arguments={"d": str(dt)}``) are matched whole and the output
    stays ``ast.parse``-able. The first string-literal argument is the target
    notebook. Only the call span is replaced, so trailing code on the same line
    (``dbutils.notebook.run("x"); more()``) is preserved.

    When the return value is consumed (something other than whitespace precedes
    the call on its line) AND nothing follows it on that line, a ``# NEEDS-REVIEW``
    is appended — ``_nb_run`` returns ``None`` whereas Databricks returns the
    child's ``dbutils.notebook.exit`` value. (The "nothing follows" guard keeps
    the inline comment from swallowing trailing code.)
    """
    out: list[str] = []
    i = 0
    n = len(source)
    _RETURN_NOTE = (
        "  # NEEDS-REVIEW: dbutils.notebook.run return value (child exit string) "
        "not modeled; _nb_run returns None"
    )
    while True:
        m = _DBUTILS_NB_RUN_OPEN.search(source, i)
        if not m:
            out.append(source[i:])
            break
        out.append(source[i:m.start()])
        # Balanced-paren scan from the opening '(' at m.end()-1.
        depth = 0
        k = m.end() - 1
        quote: str | None = None
        while k < n:
            ch = source[k]
            if quote is not None:
                if ch == "\\":
                    k += 2
                    continue
                if ch == quote:
                    quote = None
                k += 1
                continue
            if ch in ("'", '"'):
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if k >= n:  # unbalanced (truncated source) — leave the remainder as-is
            out.append(source[m.start():])
            break
        # The TARGET is the FIRST argument only — not any quoted string anywhere in
        # the call (kwargs like arguments={"k":"v"} must not be mistaken for it).
        first = _first_arg(source[m.end():k]).strip()
        sm = _STRING_ARG.match(first) if first[:1] in ("'", '"') else None
        if sm:
            replacement = f'_nb_run("{sm.group(1)}", globals())'
        elif first:
            # First arg is a variable/expression — pass it through; _nb_run resolves
            # its string value at runtime.
            replacement = f"_nb_run({first}, globals())"
        else:
            replacement = '_nb_run("", globals())'
        line_start = source.rfind("\n", 0, m.start()) + 1
        prefix = source[line_start:m.start()]
        line_end = source.find("\n", k + 1)
        if line_end == -1:
            line_end = n
        suffix = source[k + 1:line_end]
        if not first:
            replacement += "  # NEEDS-REVIEW: could not extract dbutils.notebook.run target"
        elif prefix.strip() and not suffix.strip():
            replacement += _RETURN_NOTE
        out.append(replacement)
        i = k + 1
    return "".join(out)


def notebook_dict_to_python(nb_dict: dict) -> str:
    """Translate a parsed notebook dict to Python source.

    Args:
        nb_dict: A parsed notebook JSON dict (with "cells" key).

    Returns:
        A Python source string, or "" if the notebook is malformed.
    """
    if not isinstance(nb_dict, dict):
        return ""
    cells = nb_dict.get("cells")
    if not isinstance(cells, list):
        return ""

    blocks: list[str] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        if cell.get("cell_type") != "code":
            continue
        src = cell.get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        elif not isinstance(src, str):
            continue
        if not src.strip():
            continue
        translated = _translate_cell(src)
        if translated.strip():
            blocks.append(translated)

    result = "\n\n".join(blocks)
    # Post-process: handle dbutils.notebook.run embedded in Python code.
    result = _translate_dbutils_in_python(result)
    return result


def to_python(path: str) -> str:
    """Load a .ipynb file and return a parseable Python source string.

    Returns "" for malformed or unreadable notebooks.
    """
    try:
        with open(path, encoding="utf-8") as f:
            nb_dict = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return ""
    return notebook_dict_to_python(nb_dict)


# ---------------------------------------------------------------------------
# Databricks notebook-source ``.py`` format
# ---------------------------------------------------------------------------
#
# Databricks exports a notebook to a plain ``.py`` file whose first line is
# ``# Databricks notebook source``, with ``# COMMAND ----------`` cell separators
# and magic cells stored as ``# MAGIC``-prefixed comment lines
# (``# MAGIC %sql`` / ``# MAGIC %run`` / ``# MAGIC %md`` ...). Run as plain
# Python those magics are dead comments — a ``# MAGIC %sql`` cell's SQL never
# executes, which would silently diverge from the migrated ``.ipynb`` (whose
# ``%%sql`` cell IS live). Translating the dbx ``.py`` the same way as an
# ``.ipynb`` keeps the Phase A source baseline faithful to the original notebook.

_DBX_HEADER = "# Databricks notebook source"
_DBX_CELL_SEP = re.compile(r"(?m)^#\s*COMMAND\s*-+\s*$")
_DBX_MAGIC_LINE = re.compile(r"^#\s?MAGIC\b")
_DBX_MAGIC_PREFIX = re.compile(r"^#\s?MAGIC[ \t]?")


def is_dbx_notebook_py(text: str) -> bool:
    """True if *text* is a Databricks notebook exported to a ``.py`` source file.

    Keyed on the canonical marker Databricks writes as the first non-empty line.
    """
    for line in text.lstrip().splitlines():
        if line.strip():
            return line.strip() == _DBX_HEADER
    return False


def _dbx_cell_to_python(cell_text: str) -> str:
    """Translate one dbx cell (between ``# COMMAND`` separators) to Python.

    Consecutive ``# MAGIC``-prefixed lines are un-commented into a magic-cell
    body and translated via ``_translate_cell`` (``%sql`` → ``spark.sql``,
    ``%run`` → ``_nb_run``, ``%md``/others neutralized); the ``# Databricks
    notebook source`` header is dropped; every other line (plain Python, incl.
    ``# DBTITLE`` titles) passes through unchanged.
    """
    out: list[str] = []
    magic_buf: list[str] = []

    def _flush() -> None:
        if magic_buf:
            translated = _translate_cell("\n".join(magic_buf))
            if translated.strip():
                out.append(translated)
            magic_buf.clear()

    for ln in cell_text.splitlines():
        stripped = ln.strip()
        if stripped == _DBX_HEADER:
            continue
        if _DBX_MAGIC_LINE.match(stripped):
            magic_buf.append(_DBX_MAGIC_PREFIX.sub("", stripped))
            continue
        _flush()
        out.append(ln.rstrip("\n"))
    _flush()
    return "\n".join(out)


def dbx_py_to_python(text: str) -> str:
    """Translate a Databricks notebook-source ``.py`` file to executable Python.

    Cells are split on ``# COMMAND ----------``; each is translated by
    ``_dbx_cell_to_python``; ``dbutils.notebook.run(...)`` is post-processed the
    same way as for ``.ipynb`` notebooks.
    """
    blocks: list[str] = []
    for cell in _DBX_CELL_SEP.split(text):
        translated = _dbx_cell_to_python(cell)
        if translated.strip():
            blocks.append(translated)
    result = "\n\n".join(blocks)
    result = _translate_dbutils_in_python(result)
    return result


_TRANSLATION_CACHE: dict = {}


def source_to_python(path: str) -> str:
    """Return executable Python for a notebook entrypoint on disk.

    Dispatches on format: ``.ipynb`` JSON, a Databricks notebook-source ``.py``
    (translated), or a plain ``.py`` (returned verbatim).

    Result is memoized per ``(abspath, mtime)``: workload files are immutable
    during a run, and the same file (esp. a shared ``%run`` target) is
    translated repeatedly across trials/includes otherwise.
    """
    try:
        key = (os.path.abspath(path), os.path.getmtime(path))
    except OSError:
        key = None
    if key is not None:
        cached = _TRANSLATION_CACHE.get(key)
        if cached is not None:
            return cached
    result = _source_to_python_uncached(path)
    if key is not None:
        _TRANSLATION_CACHE[key] = result
    return result


def _source_to_python_uncached(path: str) -> str:
    if path.endswith(".ipynb"):
        return to_python(path)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return ""
    if is_dbx_notebook_py(text):
        return dbx_py_to_python(text)
    return text
