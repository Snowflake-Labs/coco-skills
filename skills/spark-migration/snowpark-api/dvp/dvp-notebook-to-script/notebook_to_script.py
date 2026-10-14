"""
DVP Notebook to Script - Converts Jupyter notebooks to Python scripts.

Supports two notebook formats:
  1. **Jupyter (.ipynb)** — Standard JSON notebook format
  2. **Databricks Source (.py)** — Exported via Databricks Repos / CLI,
     identified by ``# Databricks notebook source`` header and
     ``# COMMAND ----------`` cell separators

Each notebook cell is emitted inside a single ``def run()`` function,
preserving the shared namespace behavior of Jupyter notebooks (all
variables live in the same function scope). The function is invoked from
an ``if __name__ == "__main__"`` block. Cell boundaries are marked with
Notebook.cell() calls for tracking and error reporting.

Output files are named: <original>.ipynb.py  (for .ipynb)
                         <original>.dbx.py   (for Databricks .py)
"""

import json
import re
import shutil
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# Location of the helper module (shipped alongside this converter)
HELPER_MODULE = Path(__file__).parent / "dvp_notebook_helper.py"

DBX_HEADER = "# Databricks notebook source"
DBX_SEPARATOR = "# COMMAND ----------"
DBX_MAGIC_PREFIX = "# MAGIC "


def is_databricks_source(path: Path) -> bool:
    """Check if a .py file is a Databricks-exported notebook."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            first_line = f.readline().rstrip('\n')
        return first_line == DBX_HEADER
    except (OSError, UnicodeDecodeError):
        return False


def parse_databricks_source(path: Path) -> list[dict]:
    """
    Parse a Databricks source .py file into a list of cell dicts
    compatible with the .ipynb cell format used by _generate_script().

    Returns a list of dicts with keys: cell_type, source
    """
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Remove the header line
    if content.startswith(DBX_HEADER):
        content = content[len(DBX_HEADER):]
        if content.startswith('\n'):
            content = content[1:]

    # Split on cell separator
    raw_chunks = content.split(DBX_SEPARATOR)

    cells = []
    for chunk in raw_chunks:
        # Strip a single leading/trailing newline from the separator boundary
        if chunk.startswith('\n'):
            chunk = chunk[1:]
        if chunk.endswith('\n'):
            chunk = chunk[:-1]

        if not chunk.strip():
            continue

        lines = chunk.split('\n')
        cell_type, source_text = _classify_dbx_cell(lines)
        cells.append({'cell_type': cell_type, 'source': source_text})

    return cells


def _classify_dbx_cell(lines: list[str]) -> tuple[str, str]:
    """
    Classify a Databricks cell and extract its content.

    Returns (cell_type, source_text) where cell_type is one of:
      "markdown", "code", "magic_run", "magic_sql", "magic_other"

    For magic lines the ``# MAGIC `` prefix is stripped.
    """
    content_lines = []
    has_magic = False
    magic_type = None

    for line in lines:
        if line.startswith(DBX_MAGIC_PREFIX) or line == "# MAGIC":
            has_magic = True
            stripped = line[len(DBX_MAGIC_PREFIX):] if line.startswith(DBX_MAGIC_PREFIX) else ""

            if magic_type is None:
                first = stripped.lstrip()
                if first.startswith('%md'):
                    magic_type = 'md'
                    # Strip the %md directive itself
                    after_md = first[3:]
                    if after_md and after_md[0] == ' ':
                        after_md = after_md[1:]
                    content_lines.append(after_md)
                    continue
                elif first.startswith('%run'):
                    magic_type = 'run'
                    content_lines.append(stripped)
                    continue
                elif first.startswith('%sql'):
                    magic_type = 'sql'
                    after_sql = first[4:]
                    if after_sql and after_sql[0] == ' ':
                        after_sql = after_sql[1:]
                    content_lines.append(after_sql)
                    continue
                else:
                    magic_type = 'other'
                    content_lines.append(stripped)
                    continue

            # Continuation lines for the same magic block
            if magic_type == 'md':
                # Strip the leading %md if repeated (some exports repeat it)
                if stripped.lstrip().startswith('%md'):
                    after = stripped.lstrip()[3:]
                    if after and after[0] == ' ':
                        after = after[1:]
                    content_lines.append(after)
                else:
                    content_lines.append(stripped)
            elif magic_type == 'sql':
                content_lines.append(stripped)
            else:
                content_lines.append(stripped)
        else:
            content_lines.append(line)

    source_text = '\n'.join(content_lines)

    if not has_magic:
        return ('code', source_text)

    if magic_type == 'md':
        return ('markdown', source_text)
    elif magic_type == 'run':
        return ('code', source_text)
    elif magic_type == 'sql':
        return ('code', source_text)
    else:
        return ('code', source_text)


@dataclass
class ConversionResult:
    """Result of converting a single notebook."""
    source_path: Path
    output_path: Path
    total_cells: int
    code_cells: int
    markdown_cells: int
    success: bool
    source_format: str = "ipynb"
    error: Optional[str] = None


class NotebookToScriptConverter:
    """Converts Jupyter notebooks (.ipynb) and Databricks source (.py) to Python scripts."""

    # Pattern to detect IPython magic commands
    MAGIC_PATTERN = re.compile(r'^(\s*)([%!])')

    # Matches %run references in code cells (both raw and after # MAGIC prefix)
    RUN_PATTERN = re.compile(r'%run\s+(\S+)')

    # SQL keywords that indicate a SQL cell (at start of line, case insensitive)
    SQL_KEYWORDS = re.compile(
        r'^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|'
        r'MERGE|WITH|GRANT|REVOKE|USE|SHOW|DESCRIBE|EXPLAIN|CALL|SET|UNSET|'
        r'BEGIN|COMMIT|ROLLBACK)\b',
        re.IGNORECASE
    )
    SQL_COMMENT = re.compile(r'^\s*--')

    def __init__(self, source_path: Path):
        """
        Initialize the converter.

        Args:
            source_path: Path to dvp/01-source/ directory
        """
        self.source_path = Path(source_path)
        self.results: list[ConversionResult] = []
        self._run_targets: set[str] = set()

    def find_notebooks(self) -> list[Path]:
        """Find all .ipynb files and Databricks source .py files."""
        if not self.source_path.exists():
            print(f"Warning: Source path not found: {self.source_path}")
            return []

        notebooks = list(self.source_path.rglob("*.ipynb"))
        notebooks = [nb for nb in notebooks if ".ipynb_checkpoints" not in str(nb)]

        # Scan .py files for Databricks source format
        for py_file in self.source_path.rglob("*.py"):
            if py_file.name.endswith('.dbx.py') or py_file.name.endswith('.ipynb.py'):
                continue
            if is_databricks_source(py_file):
                notebooks.append(py_file)

        return notebooks

    def scan_run_targets(self, notebooks: list[Path]) -> set[str]:
        """
        First pass: scan all notebooks for %run references.

        Returns a set of stem names (e.g. {"pipeline_config"}) that are
        referenced by %run from any notebook. These will be generated as
        flat scripts (no ``def run():`` wrapper).
        """
        targets = set()
        for nb_path in notebooks:
            refs = self._extract_run_refs(nb_path)
            for ref in refs:
                # %run ./pipeline_config → stem "pipeline_config"
                stem = Path(ref).stem
                targets.add(stem)
        self._run_targets = targets
        if targets:
            print(f"  %%run targets (will generate flat): {', '.join(sorted(targets))}")
        return targets

    def _extract_run_refs(self, notebook_path: Path) -> list[str]:
        """Extract all %run notebook references from a single notebook."""
        refs = []
        if notebook_path.suffix == '.ipynb':
            try:
                with open(notebook_path, 'r', encoding='utf-8') as f:
                    nb = json.load(f)
                for cell in nb.get('cells', []):
                    if cell.get('cell_type') != 'code':
                        continue
                    source = cell.get('source', [])
                    text = ''.join(source) if isinstance(source, list) else source
                    refs.extend(m.group(1) for m in self.RUN_PATTERN.finditer(text))
            except (json.JSONDecodeError, OSError):
                pass
        elif notebook_path.suffix == '.py' and is_databricks_source(notebook_path):
            try:
                with open(notebook_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                refs.extend(m.group(1) for m in self.RUN_PATTERN.finditer(content))
            except OSError:
                pass
        return refs

    def _is_run_target(self, notebook_path: Path) -> bool:
        """Check if a notebook is a %run target (should be generated flat)."""
        return notebook_path.stem in self._run_targets

    def convert_notebook(self, notebook_path: Path) -> ConversionResult:
        """
        Convert a single notebook (.ipynb or Databricks .py) to a Python script.

        Args:
            notebook_path: Path to the notebook file

        Returns:
            ConversionResult with details of the conversion
        """
        if notebook_path.suffix == '.py' and is_databricks_source(notebook_path):
            return self._convert_databricks(notebook_path)
        return self._convert_ipynb(notebook_path)

    def _convert_ipynb(self, notebook_path: Path) -> ConversionResult:
        """Convert a Jupyter .ipynb notebook."""
        output_path = notebook_path.parent / f"{notebook_path.name}.py"
        flat = self._is_run_target(notebook_path)

        try:
            with open(notebook_path, 'r', encoding='utf-8') as f:
                notebook = json.load(f)

            cells = notebook.get('cells', [])

            code_cells = sum(1 for c in cells if c.get('cell_type') == 'code')
            markdown_cells = sum(1 for c in cells if c.get('cell_type') == 'markdown')

            script = self._generate_script(notebook_path.name, cells, flat=flat)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(script)

            return ConversionResult(
                source_path=notebook_path,
                output_path=output_path,
                total_cells=len(cells),
                code_cells=code_cells,
                markdown_cells=markdown_cells,
                success=True,
                source_format="ipynb",
            )

        except json.JSONDecodeError as e:
            return ConversionResult(
                source_path=notebook_path,
                output_path=output_path,
                total_cells=0,
                code_cells=0,
                markdown_cells=0,
                success=False,
                source_format="ipynb",
                error=f"Invalid JSON: {e}",
            )
        except Exception as e:
            return ConversionResult(
                source_path=notebook_path,
                output_path=output_path,
                total_cells=0,
                code_cells=0,
                markdown_cells=0,
                success=False,
                source_format="ipynb",
                error=str(e),
            )

    def _convert_databricks(self, notebook_path: Path) -> ConversionResult:
        """Convert a Databricks source .py notebook."""
        output_path = notebook_path.parent / f"{notebook_path.stem}.dbx.py"
        flat = self._is_run_target(notebook_path)

        try:
            cells = parse_databricks_source(notebook_path)

            code_cells = sum(1 for c in cells if c.get('cell_type') == 'code')
            markdown_cells = sum(1 for c in cells if c.get('cell_type') == 'markdown')

            script = self._generate_script(notebook_path.name, cells, flat=flat)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(script)

            return ConversionResult(
                source_path=notebook_path,
                output_path=output_path,
                total_cells=len(cells),
                code_cells=code_cells,
                markdown_cells=markdown_cells,
                success=True,
                source_format="databricks",
            )

        except Exception as e:
            return ConversionResult(
                source_path=notebook_path,
                output_path=output_path,
                total_cells=0,
                code_cells=0,
                markdown_cells=0,
                success=False,
                source_format="databricks",
                error=str(e),
            )

    # Padding used after region label for visual separation
    REGION_PAD_CHAR = "="
    ENDREGION_PAD_CHAR = "-"
    REGION_LINE_WIDTH = 55

    # Indentation constants
    IND1 = "    "      # 4 spaces — inside def run()
    IND2 = "        "  # 8 spaces — inside def run() > try

    # Flat-mode marker embedded in generated scripts so the helper can
    # detect whether a file was generated flat (for %run exec) or wrapped.
    FLAT_MARKER = "# DVP:FLAT — this script is a %%run target (no def run wrapper)"

    def _generate_script(self, notebook_name: str, cells: list, *, flat: bool = False) -> str:
        """Generate Python script from notebook cells.

        When ``flat=False`` (default), wraps all cell code inside a
        ``def run()`` function called from ``if __name__ == "__main__"``.

        When ``flat=True`` (for %%run targets), emits cell code at module
        level so it can be ``exec()``'d into the caller's globals.
        """
        lines = []

        # Header / imports (module level)
        lines.append('"""')
        lines.append(f"Auto-generated from: {notebook_name}")
        lines.append(f"Generated by: dvp-notebook-to-script")
        lines.append(f"Generated at: {datetime.now(timezone.utc).isoformat()}")
        lines.append('"""')

        if flat:
            lines.append(self.FLAT_MARKER)
            lines.append('import sys')
            lines.append('from pathlib import Path')
            lines.append("sys.path.insert(0, str(Path(__file__).resolve().parent.parent / '03-tests'))")
            lines.append('')
            lines.append('from dvp_notebook_helper import Notebook')
            lines.append('')
            lines.append(f'nb = Notebook("{notebook_name}", __file__)')
            lines.append('')
            ind = ""  # no indentation — module level
        else:
            lines.append('import sys')
            lines.append('from pathlib import Path')
            lines.append("sys.path.insert(0, str(Path(__file__).resolve().parent.parent / '03-tests'))")
            lines.append('')
            lines.append('from dvp_notebook_helper import Notebook')
            lines.append('')
            lines.append('')
            lines.append('def run():')
            lines.append(f'{self.IND1}nb = Notebook("{notebook_name}", __file__)')
            lines.append('')
            lines.append(f'{self.IND1}try:')
            lines.append('')
            ind = self.IND2  # indented inside def run() > try

        cell_number = 0

        for i, cell in enumerate(cells):
            cell_type = cell.get('cell_type', 'unknown')
            source = cell.get('source', [])

            if isinstance(source, list):
                source_text = ''.join(source)
            else:
                source_text = source

            cell_number += 1
            cell_label = f"{cell_number:03d}"

            if cell_type == 'code':
                is_sql = self._is_sql_cell(source_text)
                cell_type_arg = '"%%sql"' if is_sql else ''
                region_suffix = " (sql)" if is_sql else ""

                region_tag = f"# region {cell_label}{region_suffix}"
                pad = f" {self.REGION_PAD_CHAR * (self.REGION_LINE_WIDTH - len(region_tag))}"
                lines.append(f"{ind}{region_tag}{pad}")
                if cell_type_arg:
                    lines.append(f'{ind}nb.cell("{cell_label}", {cell_type_arg})')
                else:
                    lines.append(f'{ind}nb.cell("{cell_label}")')
                lines.append('')

                if is_sql:
                    cell_lines = self._process_sql_cell(source_text, f"_sql_result_{cell_label}")
                else:
                    cell_lines = self._process_code_cell(source_text)

                while cell_lines and not cell_lines[0].strip():
                    cell_lines.pop(0)
                while cell_lines and not cell_lines[-1].strip():
                    cell_lines.pop()

                for line in cell_lines:
                    lines.append(f"{ind}{line}")

                endregion_tag = f"# endregion {cell_label}"
                endpad = f" {self.ENDREGION_PAD_CHAR * (self.REGION_LINE_WIDTH - len(endregion_tag))}"
                lines.append(f'{ind}{endregion_tag}{endpad}')
                lines.append('')

            elif cell_type == 'markdown':
                region_tag = f"# region {cell_label} (markdown)"
                pad = f" {self.REGION_PAD_CHAR * (self.REGION_LINE_WIDTH - len(region_tag))}"
                lines.append(f"{ind}{region_tag}{pad}")
                lines.append(f'{ind}nb.cell("{cell_label}", "markdown")')
                lines.append('')
                md_lines = source_text.split('\n')
                while md_lines and not md_lines[0].strip():
                    md_lines.pop(0)
                while md_lines and not md_lines[-1].strip():
                    md_lines.pop()
                for line in md_lines:
                    lines.append(f"{ind}# {line}")
                endregion_tag = f"# endregion {cell_label}"
                endpad = f" {self.ENDREGION_PAD_CHAR * (self.REGION_LINE_WIDTH - len(endregion_tag))}"
                lines.append(f'{ind}{endregion_tag}{endpad}')
                lines.append('')

        if flat:
            lines.append('nb.finish()')
            lines.append('')
        else:
            lines.append(f'{self.IND2}nb.finish()')
            lines.append('')
            lines.append(f'{self.IND1}except Exception as _e:')
            lines.append(f'{self.IND2}nb.report_error(_e)')
            lines.append(f'{self.IND2}raise')
            lines.append('')
            lines.append('')
            lines.append('if __name__ == "__main__":')
            lines.append(f'{self.IND1}run()')
            lines.append('')

        return '\n'.join(lines)

    def _is_sql_cell(self, source: str) -> bool:
        """
        Detect if a cell contains SQL code.
        
        SQL cells typically start with:
        - %%sql magic command
        - SQL comments (-- )
        - SQL keywords (SELECT, INSERT, CREATE, etc.)
        """
        # Get first non-empty line
        for line in source.split('\n'):
            stripped = line.strip()
            if stripped:
                # Check for %%sql magic, SQL comment (--) or SQL keyword
                if stripped.lower() == '%%sql':
                    return True
                return bool(self.SQL_COMMENT.match(stripped) or self.SQL_KEYWORDS.match(stripped))
        return False

    def _process_sql_cell(self, source: str, result_var: str) -> list[str]:
        """
        Process SQL cell content, converting %%sql magic to spark.sql() calls.
        
        Each SQL statement becomes a spark.sql("<sql>") call.
        SQL comments (--) are converted to Python comments (#).
        The result is assigned to a variable named after the cell.
        """
        lines = []
        
        # Separate SQL comments from actual SQL
        python_comments = []
        sql_lines = []
        
        for line in source.split('\n'):
            stripped = line.strip()
            
            # Skip %%sql magic line
            if stripped.lower() == '%%sql':
                continue
            
            # Skip empty lines at start (before any content)
            if not python_comments and not sql_lines and not stripped:
                continue
            
            # Check if line is a SQL comment
            if stripped.startswith('--'):
                # Convert SQL comment to Python comment
                # Remove the -- and leading space if present
                comment_text = stripped[2:].lstrip()
                python_comments.append(f"# {comment_text}" if comment_text else "#")
            else:
                sql_lines.append(line)
        
        # Remove trailing empty lines from SQL
        while sql_lines and not sql_lines[-1].strip():
            sql_lines.pop()
        
        # Add Python comments first
        for comment in python_comments:
            lines.append(comment)
        
        if python_comments:
            lines.append("")
        
        if not sql_lines:
            lines.append("pass  # empty SQL cell (comments only)")
            return lines
        
        # Add the SQL execution
        lines.append('_sql = """')
        for sql_line in sql_lines:
            lines.append(sql_line)
        lines.append('"""')
        lines.append('')
        lines.append("# Execute SQL and store result")
        lines.append(f"{result_var} = spark.sql(_sql)")
        
        return lines

    def _process_code_cell(self, source: str) -> list[str]:
        """Process code cell content, handling magic commands.

        Recognized magics:
          %run path/to/notebook $arg="val"  → nb.run("path/to/notebook", "$arg=\\"val\\"")
          %pip install pkg                  → nb.magic("%pip", "install pkg")
          %%time                            → nb.magic("%%time", "")
          !shell_cmd                        → nb.magic("!", "shell_cmd")
        """
        lines = []
        
        for line in source.split('\n'):
            stripped = line.strip()
            
            # Check for cell magic (%%magic)
            if stripped.startswith('%%'):
                magic_match = stripped[2:].split(None, 1)
                magic_kind = magic_match[0] if magic_match else ""
                magic_args = magic_match[1] if len(magic_match) > 1 else ""
                magic_args_escaped = magic_args.replace('"', '\\"')
                lines.append(f'nb.magic("%%{magic_kind}", "{magic_args_escaped}")')
            
            # Check for %run (cross-notebook execution)
            elif stripped.startswith('%run ') or stripped == '%run':
                run_args = stripped[5:].strip()
                # Split notebook path from parameters ($key="val")
                parts = run_args.split(None, 1)
                notebook_ref = parts[0] if parts else ""
                run_params = parts[1] if len(parts) > 1 else ""
                notebook_ref_escaped = notebook_ref.replace('"', '\\"')
                run_params_escaped = run_params.replace('"', '\\"')
                if run_params:
                    lines.append(f'nb.run("{notebook_ref_escaped}", "{run_params_escaped}")')
                else:
                    lines.append(f'nb.run("{notebook_ref_escaped}")')
            
            # Check for line magic (%magic) — excluding %run handled above
            elif stripped.startswith('%') and not stripped.startswith('%%'):
                magic_match = stripped[1:].split(None, 1)
                magic_kind = magic_match[0] if magic_match else ""
                magic_args = magic_match[1] if len(magic_match) > 1 else ""
                magic_args_escaped = magic_args.replace('"', '\\"')
                lines.append(f'nb.magic("%{magic_kind}", "{magic_args_escaped}")')
            
            # Check for shell command (!command)
            elif stripped.startswith('!'):
                shell_cmd = stripped[1:]
                shell_cmd_escaped = shell_cmd.replace('"', '\\"')
                lines.append(f'nb.magic("!", "{shell_cmd_escaped}")')
            
            else:
                lines.append(line)
        
        return lines

    def _find_tests_dir(self) -> Path | None:
        """Find the dvp/03-tests directory from the source path."""
        # Navigate up from source_path to find 01-source or 02-migrated*
        current = self.source_path.resolve()
        while current.name not in ('01-source', '02-migrated', '02-migrated_scos', '') and current != current.parent:
            current = current.parent
        
        if current.name in ('01-source', '02-migrated', '02-migrated_scos'):
            tests_dir = current.parent / '03-tests'
            return tests_dir
        
        return None

    def _ensure_helper(self) -> None:
        """Copy dvp_notebook_helper.py to the dvp/03-tests directory.
        
        The helper is placed once in 03-tests/ (sibling of 01-source/ and 02-migrated*/).
        Generated scripts use a relative path import: parent.parent / '03-tests'.
        """
        tests_dir = self._find_tests_dir()
        
        tests_dir.mkdir(parents=True, exist_ok=True)
        dest = tests_dir / HELPER_MODULE.name
        
        if not dest.exists() or dest.read_text() != HELPER_MODULE.read_text():
            shutil.copy2(HELPER_MODULE, dest)
            print(f"  Copied {HELPER_MODULE.name} → {tests_dir}")

    def _is_inside_dvp(self) -> bool:
        """Check if the source path is inside a dvp structure (01-source/ or 02-migrated*/)."""
        return self._find_tests_dir() is not None

    def convert_all(self) -> list[ConversionResult]:
        """Convert all notebooks in the source directory.
        
        Only runs inside a dvp structure (01-source/ or 02-migrated*/).
        The helper module is placed once in the sibling 03-tests/ directory.
        """
        if not self._is_inside_dvp():
            print(f"Error: {self.source_path} is not inside a dvp structure.")
            print(f"  The converter expects notebooks to be in dvp/01-source/ or dvp/02-migrated*/ (including dvp/02-migrated_scos/).")
            print(f"  Please run the dvp-orchestrator first to set up the dvp workspace.")
            return []

        notebooks = self.find_notebooks()

        if not notebooks:
            print("No notebooks found to convert.")
            return []

        ipynb_count = sum(1 for n in notebooks if n.suffix == '.ipynb')
        dbx_count = sum(1 for n in notebooks if n.suffix == '.py')

        parts = []
        if ipynb_count:
            parts.append(f"{ipynb_count} .ipynb")
        if dbx_count:
            parts.append(f"{dbx_count} Databricks .py")
        print(f"Found {len(notebooks)} notebook(s) to convert ({', '.join(parts)}).")

        # First pass: detect %run targets so we generate them flat
        self.scan_run_targets(notebooks)

        for notebook_path in notebooks:
            relative_path = notebook_path.relative_to(self.source_path)
            fmt = "dbx" if (notebook_path.suffix == '.py' and is_databricks_source(notebook_path)) else "ipynb"
            flat_tag = " (flat/%%run target)" if self._is_run_target(notebook_path) else ""
            print(f"  Converting [{fmt}]{flat_tag}: {relative_path}")
            
            result = self.convert_notebook(notebook_path)
            self.results.append(result)
            
            if result.success:
                print(f"    → {result.output_path.name} ({result.code_cells} code, {result.markdown_cells} markdown)")
            else:
                print(f"    ✗ Error: {result.error}")

        # Copy helper module to 03-tests directory (once)
        if any(r.success for r in self.results):
            self._ensure_helper()

        return self.results

    def print_summary(self) -> None:
        """Print conversion summary."""
        successful = [r for r in self.results if r.success]
        failed = [r for r in self.results if not r.success]
        
        ipynb_ok = [r for r in successful if r.source_format == "ipynb"]
        dbx_ok = [r for r in successful if r.source_format == "databricks"]

        print("\n" + "=" * 50)
        print("Notebook Conversion Summary")
        print("=" * 50)
        print(f"Total notebooks: {len(self.results)}")
        print(f"  Converted: {len(successful)}")
        if ipynb_ok:
            print(f"    .ipynb:      {len(ipynb_ok)}")
        if dbx_ok:
            print(f"    Databricks:  {len(dbx_ok)}")
        print(f"  Failed: {len(failed)}")
        
        if successful:
            print("\nConverted notebooks:")
            for r in successful:
                relative = r.source_path.relative_to(self.source_path)
                fmt_tag = f"[{r.source_format}]"
                print(f"  ✓ {fmt_tag:13s} {relative} → {r.output_path.name}")
                print(f"                {r.code_cells} code cells, {r.markdown_cells} markdown cells")
        
        if failed:
            print("\nFailed conversions:")
            for r in failed:
                relative = r.source_path.relative_to(self.source_path)
                print(f"  ✗ {relative}: {r.error}")
        
        print("=" * 50)
        
        if successful:
            print("\nNext: Run dvp-entrypoint-identifier to detect entry points.")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert Jupyter notebooks to Python scripts"
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to dvp/01-source/ directory"
    )

    args = parser.parse_args()

    converter = NotebookToScriptConverter(source_path=args.source)
    converter.convert_all()
    converter.print_summary()


if __name__ == "__main__":
    main()