#!/usr/bin/env python3
"""
SNOW-3383532: Deterministic fallback transformation for files not processed by LLM sub-agents.

NOTE (deprecated from the automatic pipeline): this script is NO LONGER run as
a mandatory pre-Phase-3 hard gate by ``orchestrate_phases.py``. The mechanical
transform it performs (header, import annotations, session-init replacement) is
now done once, deterministically, by Phase 3 (``scripts/update_imports.py``)
over *every* manifest file — so running fallback first only stamped a premature
"not processed by the LLM fixer agent" header that suppressed Phase 3's real
header. Files the LLM fixer skips are still caught: Phase 2c
(``verify_migration.py``) flags them as partial from evidence, and Phase 3
applies the mechanical floor. This module is retained as an OPTIONAL manual
gap-filler, and for the shared helpers / EWI constants that other scripts
import (verify_migration.py, update_imports.py).

When invoked manually, for each unprocessed manifest file it:
  1. Copies the original source to Output/ (if not already present)
  2. Injects a migration header comment (the "not processed by the LLM fixer
     agent" signature — the evidence verify_migration.py reads)
  3. Rewrites pyspark / spark imports with annotated SCOS comments
  4. Replaces SparkSession.builder with snowpark_connect.init_spark_session()

This script performs the mechanical transform ONLY. It does NOT write
Partial Migration findings into analysis.json. Deciding which files are
genuinely partial (vs. successfully migrated, vs. trivial) is evidence-based
and owned by verify_migration.py. Having both write the findings produced
conflicting, duplicated entries.

Usage (manual only):
    python fallback_transform.py --state /path/to/migration_state.json

Returns exit code 0 always (failures are logged, not fatal).
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from notebook_io import (
    Cell,
    MIGRATION_HEADER_MARKER,
    detect_format,
    parse_notebook,
    write_notebook,
)

# SNOW-3383532: EWI code for "file not fully migrated by LLM agent". Kept for
# the header text and informational logging. The fallback no longer writes
# Partial Migration findings into analysis.json — that is owned by
# verify_migration.py, which decides partial-vs-migrated from on-disk evidence
# (see _human_action_entry / reconcile). Having both write caused conflicting,
# duplicated entries.
FALLBACK_EWI_CODE_PY = "SPRKCNTPY0099"
FALLBACK_EWI_CODE_SCALA = "SPRKCNTSCL0099"

# Ordered import rewrite rules per language: (pattern, annotation_comment)
IMPORT_REWRITE_RULES_PY = [
    # pyspark top-level
    (r"^(from pyspark(?:\.\S+)?\s+import\s+.+)$",
     "# SCOS: [SPRKCNTPY0099] PySpark import — review for Spark Connect compatibility"),
    (r"^(import pyspark(?:\.\S+)?)(.*)$",
     "# SCOS: [SPRKCNTPY0099] PySpark import — review for Spark Connect compatibility"),
    # databricks
    (r"^(from databricks(?:\.\S+)?\s+import\s+.+)$",
     "# SCOS: [SPRKCNTPY0099] Databricks import — not available in Snowpark Connect"),
    (r"^(import databricks(?:\.\S+)?)(.*)$",
     "# SCOS: [SPRKCNTPY0099] Databricks import — not available in Snowpark Connect"),
    # delta
    (r"^(from delta(?:\.\S+)?\s+import\s+.+)$",
     "# SCOS: [SPRKCNTPY0099] Delta Lake import — replace with Snowflake table operations"),
    (r"^(import delta(?:\.\S+)?)(.*)$",
     "# SCOS: [SPRKCNTPY0099] Delta Lake import — replace with Snowflake table operations"),
]

IMPORT_REWRITE_RULES_SCALA = [
    (r"^(import\s+org\.apache\.spark(?:\.\S+)?)(.*)$",
     "// SCOS: [SPRKCNTSCL0099] Spark import — review for Spark Connect compatibility"),
    (r"^(import\s+com\.databricks(?:\.\S+)?)(.*)$",
     "// SCOS: [SPRKCNTSCL0099] Databricks import — not available in Snowpark Connect"),
    (r"^(import\s+io\.delta(?:\.\S+)?)(.*)$",
     "// SCOS: [SPRKCNTSCL0099] Delta Lake import — replace with Snowflake table operations"),
]

# SparkSession.builder patterns → Spark Connect replacement
SESSION_PATTERNS_PY = [
    (
        "DatabricksSession.builder.getOrCreate()",
        "snowpark_connect.init_spark_session()",
    ),
    (
        "SparkSession.builder.master(master).appName(app_name).getOrCreate()",
        "snowpark_connect.init_spark_session()",
    ),
    (
        "SparkSession.builder.appName(app_name).getOrCreate()",
        "snowpark_connect.init_spark_session()",
    ),
    (
        "SparkSession.builder.getOrCreate()",
        "snowpark_connect.init_spark_session()",
    ),
]

SESSION_PATTERNS_SCALA = [
    (
        "SparkSession.builder().getOrCreate()",
        "SnowparkConnectSession.builder().getOrCreate()",
    ),
    (
        "SparkSession.builder.getOrCreate()",
        "SnowparkConnectSession.builder().getOrCreate()",
    ),
]

# Import required by the SCOS Scala session form above. Injected by
# replace_session_init when a Scala session replacement is made, so the
# fallback output is self-sufficient and matches what the Phase 3
# import-updater and verify_phase.py Phase 3 gate expect (canonical
# SnowparkConnectSession.builder(); NOT vanilla SparkSession...remote()).
SESSION_IMPORT_SCALA = "import com.snowflake.snowpark_connect.client.SnowparkConnectSession"


def _lang_config(language: str) -> dict:
    """Return per-language configuration for imports, sessions, and EWI codes."""
    if language == "scala":
        return {
            "ewi_code": FALLBACK_EWI_CODE_SCALA,
            "import_rules": IMPORT_REWRITE_RULES_SCALA,
            "session_patterns": SESSION_PATTERNS_SCALA,
            "session_import": SESSION_IMPORT_SCALA,
            "comment_prefix": "// ",
            "header_style": "scala",
        }
    return {
        "ewi_code": FALLBACK_EWI_CODE_PY,
        "import_rules": IMPORT_REWRITE_RULES_PY,
        "session_patterns": SESSION_PATTERNS_PY,
        "comment_prefix": "# ",
        "header_style": "python",
    }


def load_state(state_path: str) -> dict:
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state_path: str, state: dict) -> None:
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _collect_done_files(state: dict) -> set[str]:
    """Union every place a fixer/coordinator records "this file is done".

    SNOW-3383532 follow-up: the fixer agent and the coordinator do NOT all
    write completion to a single key. Observed in the wild:
    - ``processed_files`` (top-level) — written by ``agents/fixer.md`` after a
      chunk completes.
    - ``2_fixes.files_done`` (top-level) — the public-skill contract this
      function originally relied on.
    - ``phases_completed.2_fixes.files_done`` — where some runs record the
      per-phase completion list.

    Reading only one of these (as the original code did) makes the fallback
    treat already-migrated files as unprocessed, then stamp them with a
    bogus ``SPRKCNTPY0099`` "Partial Migration" finding and a "not processed
    by the LLM" header. Mirror the orchestrator's own ``get_processed_files``
    by unioning all of them.
    """
    done: set[str] = set()
    done |= set(state.get("processed_files", []))
    done |= set(state.get("2_fixes", {}).get("files_done", []))
    done |= set(
        state.get("phases_completed", {}).get("2_fixes", {}).get("files_done", [])
    )
    return done


def find_unprocessed_files(state: dict) -> list[str]:
    """Return manifest entries not yet processed by the fixer agent.

    A file is "unprocessed" only when NO completion record references it.
    Completion is collected from every key a fixer/coordinator may write
    (see :func:`_collect_done_files`), matched by both full path and
    basename so abs/relative path-shape differences don't cause a
    already-done file to be re-stamped.

    The ``pending_files`` key (private/chunked skill) lists what is still
    pending; when present it is authoritative, but we still subtract any
    file that also carries a completion record so a stale pending entry
    cannot re-trigger fallback on an already-migrated file.

    Falls back to returning all manifest files only when there is genuinely
    no completion signal anywhere (safe first-run default).
    """
    manifest: list[str] = state.get("manifest", [])
    done_set = _collect_done_files(state)
    done_basenames = {os.path.basename(p) for p in done_set}

    def _is_done(entry: str) -> bool:
        return entry in done_set or os.path.basename(entry) in done_basenames

    # Private-skill style: pending_files lists what's still pending, but
    # never re-stamp a file that also has a completion record.
    pc = state.get("phases_completed", {}) or {}
    if "pending_files" in state:
        return [f for f in state["pending_files"] if not _is_done(f)]
    nested_pending = (pc.get("2_fixes", {}) or {}).get("pending_files")
    if nested_pending is not None:
        return [f for f in nested_pending if not _is_done(f)]

    return [entry for entry in manifest if not _is_done(entry)]


def is_entry_point(content: str) -> bool:
    """Heuristic: file is an entry point if it contains SparkSession.builder."""
    return bool(re.search(r"SparkSession\s*\.\s*builder|DatabricksSession\s*\.\s*builder", content))


def _build_header_text(filename: str, header_style: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    if header_style == "scala":
        return (
            "/*\n"
            f" * {MIGRATION_HEADER_MARKER}\n"
            " * =====================\n"
            f" * Source File: {filename}\n"
            f" * Migrated on: {today}\n"
            " *\n"
            " * Changes Overview:\n"
            " * - Deterministic fallback transformation applied (SNOW-3383532)\n"
            " * - LLM agent did not fully process this file; imports annotated manually\n"
            " *\n"
            " * Known Limitations:\n"
            " * - Manual review required — this file was not processed by the LLM fixer agent\n"
            " */\n"
        )
    return (
        '"""\n'
        f"{MIGRATION_HEADER_MARKER}\n"
        "=====================\n"
        f"Source File: {filename}\n"
        f"Migrated on: {today}\n"
        "\n"
        "Changes Overview:\n"
        "- Deterministic fallback transformation applied (SNOW-3383532)\n"
        "- LLM agent did not fully process this file; imports annotated manually\n"
        "\n"
        "Known Limitations:\n"
        "- Manual review required — this file was not processed by the LLM fixer agent\n"
        '"""\n'
    )


def add_migration_header(content: str, filename: str, header_style: str = "python") -> str:
    """Prepend a SCOS migration header comment if not already present."""
    if MIGRATION_HEADER_MARKER in content[:500]:
        return content
    return _build_header_text(filename, header_style) + content


def rewrite_imports(content: str, import_rules: list) -> tuple[str, int]:
    """Annotate imports matching ``import_rules`` with SCOS comment lines.

    Returns (modified_content, count_of_rewrites).
    """
    lines = content.splitlines(keepends=True)
    count = 0
    new_lines = []

    for line in lines:
        stripped = line.rstrip("\n")
        for pattern, annotation in import_rules:
            if re.match(pattern, stripped.lstrip()):
                # Don't double-annotate
                if "SCOS:" not in stripped:
                    indent = len(stripped) - len(stripped.lstrip())
                    new_lines.append(" " * indent + annotation + "\n")
                    count += 1
                break
        new_lines.append(line)

    return "".join(new_lines), count


def _inject_import(content: str, import_stmt: str) -> str:
    """Insert ``import_stmt`` after the last top-level import line.

    Falls back to the top of the file when no import is present. No-op if the
    statement is already there (caller also guards, but keep this safe).
    """
    if import_stmt in content:
        return content
    lines = content.splitlines(keepends=True)
    last_import = -1
    for i, line in enumerate(lines):
        if re.match(r"\s*import\s+\S", line):
            last_import = i
    stmt = import_stmt + "\n"
    if last_import >= 0:
        if not lines[last_import].endswith("\n"):
            lines[last_import] = lines[last_import] + "\n"
        lines.insert(last_import + 1, stmt)
    else:
        lines.insert(0, stmt)
    return "".join(lines)


def replace_session_init(
    content: str, session_patterns: list, session_import: str | None = None
) -> tuple[str, bool]:
    """Replace SparkSession.builder patterns using the language's rule list.

    When ``session_import`` is provided and a replacement is made, that import
    is injected (after the last top-level import, else at the top) unless
    already present — so the rewritten session call resolves on its own instead
    of depending on a later LLM import-updater pass.

    Returns (modified_content, was_replaced).
    """
    replaced = False
    for search, replace_with in session_patterns:
        if search in content:
            content = content.replace(search, replace_with, 1)
            replaced = True
            break  # only replace first/most-specific match
    if replaced and session_import:
        content = _inject_import(content, session_import)
    return content, replaced


def _transform_notebook(
    source_path: str, output_path: str, cfg: dict, info: dict | None = None
) -> dict:
    """Apply fallback transformation to a notebook file, in-place in its format.

    - Injects a SCOS header cell (markdown) at index 0 if missing.
    - Annotates imports / replaces session init only in code cells whose
      ``cell_language`` matches the target language — other cells
      (markdown, SQL, cross-language) are left untouched.
    """
    result = {
        "file": os.path.basename(output_path),
        "copied": False,
        "header_added": False,
        "imports_rewritten": 0,
        "session_replaced": False,
        "notebook_cells_processed": 0,
        "error": None,
    }

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        if not os.path.exists(output_path):
            shutil.copy2(source_path, output_path)
            result["copied"] = True

        nb = parse_notebook(output_path, info=info)

        target_lang = "scala" if cfg["header_style"] == "scala" else "python"
        header_already_present = any(
            cell.cell_type == "markdown" and MIGRATION_HEADER_MARKER in cell.source
            for cell in nb.cells
        )
        if not header_already_present:
            header_text = _build_header_text(os.path.basename(output_path), cfg["header_style"])
            # Strip any language-specific syntax (docstring / block comment markers)
            # and present as plain markdown.
            if cfg["header_style"] == "scala":
                # Strip any leading Scala block-comment punctuation:
                #   "/*", "*/", " * ", " *" — including the closing " */"
                # token whose stray "/" would otherwise survive a sequential
                # removeprefix chain.
                md_body = "\n".join(
                    re.sub(r"^\s*(?:/\*|\*/|\*)\s?", "", line)
                    for line in header_text.splitlines()
                ).strip()
            else:
                md_body = header_text.replace('"""\n', "").strip()
            _prepend_markdown_cell(nb, md_body)
            result["header_added"] = True

        # Identify the single entry-point cell (first cell in target language
        # whose current source matches `is_entry_point`). Session-init
        # replacement runs only on THAT cell so a notebook that re-creates the
        # session later won't get over-rewritten, and a notebook whose entry
        # point lives in cell N (not cell 0) still gets replaced.
        entry_cell_idx: int | None = None
        for cell in nb.cells:
            if cell.cell_type != "code" or cell.cell_language != target_lang:
                continue
            if is_entry_point(cell.source):
                entry_cell_idx = cell.index
                break

        # Process each code cell whose language matches the target.
        for cell in nb.cells:
            if cell.cell_type != "code":
                continue
            if cell.cell_language != target_lang:
                continue
            new_source, import_count = rewrite_imports(cell.source, cfg["import_rules"])
            session_replaced_here = False
            if entry_cell_idx is not None and cell.index == entry_cell_idx:
                new_source, session_replaced_here = replace_session_init(
                    new_source, cfg["session_patterns"], cfg.get("session_import")
                )
            if new_source != cell.source:
                cell.source = new_source
            result["imports_rewritten"] += import_count
            if session_replaced_here:
                result["session_replaced"] = True
            result["notebook_cells_processed"] += 1

        write_notebook(output_path, nb)

    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)

    return result


def _prepend_markdown_cell(nb, body: str) -> None:
    """Insert a markdown cell containing ``body`` at the top of ``nb``.

    Handles all three notebook formats. Cells' ``index`` values are renumbered
    so downstream references remain consistent.
    """
    if nb.format == "ipynb":
        new_raw = {
            "cell_type": "markdown",
            "metadata": {},
            "source": [body],
        }
        nb._raw.setdefault("cells", []).insert(0, new_raw)
        new_cell = Cell(
            index=0,
            cell_type="markdown",
            cell_language="markdown",
            source=body,
            metadata={},
            _raw=new_raw,
            _original_source=body,
        )
    elif nb.format == "native_json":
        # Databricks native JSON: add a new command with %md prefix.
        md_source = body if body.lstrip().startswith("%md") else "%md\n" + body
        # Choose a position strictly less than the smallest existing command's
        # position so this cell sorts first in tools that re-order by
        # position. Databricks typically uses floats like 1.0, 1.5, 2.0.
        # Use ``min / 2`` (halfway to zero) so we stay strictly less than the
        # current minimum AND non-negative — Databricks re-imports and some
        # UIs treat negative positions as degenerate/zero. Fall through to 0
        # when the minimum is already <= 0 or the notebook is empty.
        existing_positions = [
            c.get("position")
            for c in nb._raw.get("commands", [])
            if isinstance(c.get("position"), (int, float))
        ]
        if existing_positions:
            current_min = min(existing_positions)
            new_position = current_min / 2 if current_min > 0 else 0
        else:
            new_position = 0

        # Every Databricks cell carries a nuid; omitting it breaks re-import.
        import uuid as _uuid
        new_nuid = _uuid.uuid4().hex

        # Mirror any `guid` usage already present in the notebook so the
        # inserted cell doesn't look structurally different from its peers.
        sample_cmd = next(iter(nb._raw.get("commands", [])), {})
        extra_fields: dict = {}
        if isinstance(sample_cmd, dict) and "guid" in sample_cmd:
            extra_fields["guid"] = _uuid.uuid4().hex

        new_cmd = {
            "version": "CommandV1",
            "subtype": "command",
            "commandType": "auto",
            "position": new_position,
            "command": md_source,
            "commandTitle": "",
            "showCommandTitle": False,
            "hideCommandCode": False,
            "hideCommandResult": False,
            "nuid": new_nuid,
            **extra_fields,
        }
        nb._raw.setdefault("commands", []).insert(0, new_cmd)
        new_cell = Cell(
            index=0,
            cell_type="markdown",
            cell_language="markdown",
            source=md_source,
            metadata={"nuid": new_nuid},
            _raw=new_cmd,
            _original_source=md_source,
        )
    else:  # exported_text
        cfg = {
            "python": ("%md", "# MAGIC "),
            "scala": ("%md", "// MAGIC "),
        }[nb.language]
        md_source = body if body.lstrip().startswith("%md") else f"{cfg[0]}\n{body}"
        # Segment-shape contract (from notebook_io._parse_exported_text):
        # the writer joins segments with "\n<sep>\n", so interior segments
        # must NOT carry leading/trailing newlines of their own. The first
        # segment carries a leading "\n" only when the file opens with the
        # Databricks header (because parse prepended the header's trailing
        # newline to the body before splitting).
        #
        # The previous form ("\n" + body + "\n") emitted two blank lines
        # between the synthetic header and the first original cell on every
        # fallback run. Build the new segment to match the first-segment
        # shape (leading "\n" when opens_with_header, no trailing "\n"), and
        # strip the leading "\n" off the previous first segment — which is
        # now interior — so the joiner doesn't double-up newlines.
        new_body = cfg[1] + ("\n" + cfg[1]).join(md_source.splitlines())
        leading = "\n" if nb._exported_opens_with_header else ""
        new_segment = leading + new_body
        if (
            nb._exported_opens_with_header
            and nb._exported_raw_segments
            and nb._exported_raw_segments[0].startswith("\n")
        ):
            nb._exported_raw_segments[0] = nb._exported_raw_segments[0][1:]
        nb._exported_raw_segments.insert(0, new_segment)
        new_cell = Cell(
            index=0,
            cell_type="markdown",
            cell_language="markdown",
            source=md_source,
            metadata={},
            _original_source=md_source,
            _exported={"kind": "magic", "segment": new_segment, "dbtitle": None},
        )

    # Renumber subsequent cells' index.
    for i, existing in enumerate(nb.cells, start=1):
        existing.index = i
    nb.cells.insert(0, new_cell)


def transform_file(
    source_path: str,
    output_path: str,
    filename: str,
    language: str = "python",
) -> dict:
    """Apply all fallback transforms to one file. Returns a result dict.

    Dispatches to the notebook path when the file is a Databricks or Jupyter
    notebook; otherwise runs the original flat-text transformation.
    """
    cfg = _lang_config(language)

    # When a prior partial run already wrote an output file, parse that
    # instead of re-copying from source — this keeps the transform
    # resumable. The ternary is pulled into a named local so the
    # notebook dispatch below reads cleanly.
    probe = output_path if os.path.exists(output_path) else source_path
    # Capture detection once and pass it through to _transform_notebook so
    # parse_notebook doesn't re-open the file to re-detect the format.
    probe_info = detect_format(probe)
    if probe_info.get("format") != "not_notebook":
        return _transform_notebook(source_path, output_path, cfg, info=probe_info)

    result = {
        "file": filename,
        "copied": False,
        "header_added": False,
        "imports_rewritten": 0,
        "session_replaced": False,
        "error": None,
    }

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        if not os.path.exists(output_path):
            shutil.copy2(source_path, output_path)
            result["copied"] = True

        with open(output_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        original = content

        content = add_migration_header(content, filename, cfg["header_style"])
        if content != original:
            result["header_added"] = True

        content, import_count = rewrite_imports(content, cfg["import_rules"])
        result["imports_rewritten"] = import_count

        if is_entry_point(content):
            content, replaced = replace_session_init(
                content, cfg["session_patterns"], cfg.get("session_import")
            )
            result["session_replaced"] = replaced

        if content != original or result["copied"]:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)

    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SNOW-3383532: Deterministic fallback for unprocessed migration files"
    )
    parser.add_argument(
        "--state", required=True,
        help="Path to migration_state.json",
    )
    parser.add_argument(
        "--language",
        choices=["python", "scala", "java"],
        default="python",
        help="Source language (default: python; selects EWI codes and import/session rules)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be done without modifying files",
    )
    args = parser.parse_args()

    state_path = os.path.abspath(args.state)
    if not os.path.exists(state_path):
        print(f"ERROR: migration_state.json not found: {state_path}", file=sys.stderr)
        return 1

    state = load_state(state_path)
    conversion_root = state.get("conversion_root", os.path.dirname(state_path))
    migrated_dir = state.get("migrated_dir", os.path.join(conversion_root, "Output"))
    analysis_path = os.path.join(conversion_root, "analysis.json")

    # Resolve original source directory (parent of Output/)
    source_dir = os.path.dirname(migrated_dir.rstrip("/"))

    unprocessed = find_unprocessed_files(state)

    ewi_code = FALLBACK_EWI_CODE_SCALA if args.language == "scala" else FALLBACK_EWI_CODE_PY

    print("SCOS Deterministic Fallback Transformation")
    print("==========================================")
    print(f"  State:         {state_path}")
    print(f"  Source dir:    {source_dir}")
    print(f"  Output dir:    {migrated_dir}")
    print(f"  Analysis:      {analysis_path}")
    print(f"  Language:      {args.language} (EWI code {ewi_code})")
    print(f"  Unprocessed:   {len(unprocessed)} file(s)")
    print()

    if not unprocessed:
        print("All manifest files were processed by sub-agents. No fallback needed.")
        state["fallback_processed"] = []
        state["fallback_count"] = 0
        if not args.dry_run:
            save_state(state_path, state)
        return 0

    if args.dry_run:
        for f in unprocessed:
            print(f"  DRY-RUN: would transform {f}")
        print(f"\n{len(unprocessed)} file(s) would be fallback-transformed.")
        return 0

    fallback_processed = []
    total_imports = 0
    total_sessions = 0
    errors = []

    for rel_path in unprocessed:
        # rel_path may be absolute or relative to source
        if os.path.isabs(rel_path):
            source_file = rel_path
            try:
                display_rel = os.path.relpath(rel_path, source_dir)
            except ValueError:
                display_rel = os.path.basename(rel_path)
            out_file = os.path.join(migrated_dir, display_rel)
        else:
            source_file = os.path.join(source_dir, rel_path)
            out_file = os.path.join(migrated_dir, rel_path)
            display_rel = rel_path

        if not os.path.exists(source_file):
            # Try migrated dir — file may already have been copied but not fixed
            source_file = out_file

        if not os.path.exists(source_file):
            print(f"  SKIP  {display_rel} — source file not found")
            errors.append(display_rel)
            continue

        result = transform_file(source_file, out_file, os.path.basename(display_rel), language=args.language)

        if result["error"]:
            print(f"  ERROR {display_rel} — {result['error']}")
            errors.append(display_rel)
            continue

        fallback_processed.append(rel_path)
        total_imports += result["imports_rewritten"]
        if result["session_replaced"]:
            total_sessions += 1

        flags = []
        if result["copied"]:
            flags.append("copied")
        if result["header_added"]:
            flags.append("header")
        if result["imports_rewritten"]:
            flags.append(f"{result['imports_rewritten']} import(s) annotated")
        if result["session_replaced"]:
            flags.append("session replaced")
        print(f"  DONE  {display_rel} [{', '.join(flags) or 'no-op'}]")

    print()
    print(f"Fallback complete: {len(fallback_processed)} file(s) transformed, "
          f"{total_imports} import(s) annotated, {total_sessions} session init(s) replaced.")
    if errors:
        print(f"Errors: {len(errors)} file(s) skipped — {errors}")

    # Update migration_state.json
    state["fallback_processed"] = fallback_processed
    state["fallback_count"] = len(fallback_processed)
    if errors:
        state["fallback_errors"] = errors
    save_state(state_path, state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
