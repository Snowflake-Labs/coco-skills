"""Phase 0.5: Deterministic Pre-Processing.

Apply every registered LibCST recipe under ``scripts/recipes/``
unconditionally to every manifest file before the LLM analyzer runs in
Phase 1. The recipe step is the always-on twin of the LLM fixer:

  * Recipes solve the easy cases byte-for-byte (e.g. preserving every
    ``SparkSession.builder.config(...)`` call when rewriting the builder
    chain).
  * The LLM in Phase 1 / Phase 2 then focuses on the genuinely hard,
    judgment-needing patterns — without burning tokens on what a 0.1 s
    syntax-tree rewrite can do reliably.

The script:

  1. Reads ``manifest`` and ``migrated_dir`` from ``migration_state.json``.
  2. Discovers every recipe under ``scripts/recipes/<recipe_name>/recipe.py``.
  3. For each manifest file, runs every recipe in deterministic
     (alphabetical) order over the file's content in ``migrated_dir``.
  4. Writes rewritten content back to ``migrated_dir`` in place — so the
     analyzer in Phase 1 naturally sees the recipe-rewritten code.
  5. Records every edit in ``migration_state.json`` under a top-level
     ``recipe_edits`` block so the analyzer and fixer can tell
     "recipe-managed" regions apart from raw source.
  6. Records its own completion under
     ``phases_completed["0_5_preprocess"]`` with a deterministic summary.

Per-file recipe failures are logged and skipped by default (best effort);
pass ``--strict`` to fail the whole phase on the first per-file error.

Usage:
    python scripts/preprocess_recipes.py --state <CONVERSION>/migration_state.json
    python scripts/preprocess_recipes.py --state ... --dry-run
    python scripts/preprocess_recipes.py --state ... --strict
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
RECIPES_DIR = os.path.join(SCRIPTS_DIR, "recipes")

# ``recipes/`` packages itself onto sys.path inside ``_common.py`` for the
# ``_recipe_base`` import, but we still need the parent ``scripts/`` dir on
# sys.path so we can ``import recipes._common`` here.
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from notebook_io import (  # noqa: E402
    detect_format,
    parse_notebook,
    write_notebook,
)
from precompile_check import run_precompile_check  # noqa: E402


def _is_parseable_python(source: str) -> bool:
    """True if ``source`` is syntactically valid Python.

    Recipes parse with ``cst.parse_module``; an IPython magic / shell-escape
    line (``%sql``, ``!ls``, ``%%time``) is not valid Python and would make
    every recipe raise on the cell. We pre-check with the stdlib compiler so
    such cells are skipped cleanly and left byte-identical.
    """
    try:
        compile(source, "<cell>", "exec")
        return True
    except (SyntaxError, ValueError):
        return False


def _is_notebook_entry(abs_path: str) -> bool:
    """True if ``abs_path`` is any notebook format ``notebook_io`` recognises
    (``.ipynb``, Databricks-native ``.python``/``.scala``/``.sql``, or a
    Databricks-exported ``.py``)."""
    try:
        return detect_format(abs_path).get("format") != "not_notebook"
    except Exception:  # noqa: BLE001
        return False


def discover_recipes() -> list[tuple[str, Any]]:
    """Return ``[(recipe_id, recipe_module), ...]`` sorted by recipe_id.

    A recipe is any ``scripts/recipes/<name>/recipe.py`` whose loaded module
    exposes both ``RECIPE_ID`` and a callable ``apply``. Helper files with
    leading underscores (``_common.py``, ``_recipe_base.py``) are skipped.
    """
    if not os.path.isdir(RECIPES_DIR):
        return []

    from recipes import _common  # type: ignore

    found: list[tuple[str, Any]] = []
    for name in sorted(os.listdir(RECIPES_DIR)):
        if name.startswith("_"):
            continue
        recipe_dir = os.path.join(RECIPES_DIR, name)
        if not os.path.isdir(recipe_dir):
            continue
        if not os.path.exists(os.path.join(recipe_dir, "recipe.py")):
            continue
        try:
            mod = _common.load_recipe_module(recipe_dir)
        except Exception as exc:  # noqa: BLE001
            print(
                f"  WARN: failed to load recipe {name!r}: {exc}",
                file=sys.stderr,
            )
            continue
        recipe_id = getattr(mod, "RECIPE_ID", None) or name
        if not callable(getattr(mod, "apply", None)):
            print(
                f"  WARN: recipe {name!r} has no callable apply(); skipped",
                file=sys.stderr,
            )
            continue
        found.append((recipe_id, mod))

    return found


def load_state(state_path: str) -> dict:
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state_path: str, state: dict) -> None:
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def resolve_file_path(rel_or_abs: str, migrated_dir: str) -> str:
    """Mirror orchestrate_phases.resolve_file_path: accept absolute paths
    or paths relative to ``migrated_dir``."""
    if os.path.isabs(rel_or_abs):
        return rel_or_abs
    return os.path.join(migrated_dir, rel_or_abs)


def _edit_to_dict(edit: Any, *, cell_index: int | None = None) -> dict:
    """Normalise ``RecipeEdit`` (or any dataclass / dict / object with the
    canonical fields) into a JSON-serialisable dict.

    The shape is the audit-trail contract that downstream consumers
    (analyzer, fixer) read. For notebook cells, ``cell_index`` is attached so
    the (otherwise cell-relative) ``src_line`` is interpretable as a
    navigable (cell, line) locator rather than a meaningless flat line."""
    if isinstance(edit, dict):
        d = edit
    elif is_dataclass(edit):
        d = asdict(edit)
    else:
        d = {
            k: getattr(edit, k, None)
            for k in ("file", "src_line", "recipe_id", "output_line_anchor")
        }
    out = {
        "recipe_id": d.get("recipe_id"),
        "src_line": d.get("src_line"),
        "output_line_anchor": d.get("output_line_anchor"),
    }
    if cell_index is not None:
        out["cell_index"] = cell_index
    return out


def _run_recipes_on_source(
    content: str,
    rel_path: str,
    recipes: list[tuple[str, Any]],
    *,
    facts_db: str | None,
    strict: bool,
    cell_index: int | None = None,
) -> tuple[str, list[dict], list[str]]:
    """Run the full recipe chain over a single Python source string.

    Shared by both the flat-``.py`` path and the per-cell notebook path.
    Recipes compose: the rewritten source from each recipe is fed into the
    next. Returns ``(new_content, edits, recipe_ids_applied)``. Per-recipe
    errors are logged and skipped unless ``strict`` (then re-raised).

    A non-Python source (e.g. an IPython magic cell) makes ``cst.parse_module``
    raise inside the recipe; that is caught here per-recipe and the source is
    left byte-identical.
    """
    all_edits: list[dict] = []
    recipe_ids_applied: list[str] = []

    for recipe_id, mod in recipes:
        try:
            result = mod.apply(content, file=rel_path, facts_db=facts_db)
        except TypeError:
            # Recipe with simpler signature.
            try:
                result = mod.apply(content, file=rel_path)
            except Exception as exc:  # noqa: BLE001
                msg = f"  ERROR {rel_path}: recipe {recipe_id!r} raised {type(exc).__name__}: {exc}"
                print(msg, file=sys.stderr)
                if strict:
                    raise
                continue
        except Exception as exc:  # noqa: BLE001
            msg = f"  ERROR {rel_path}: recipe {recipe_id!r} raised {type(exc).__name__}: {exc}"
            print(msg, file=sys.stderr)
            if strict:
                raise
            continue

        if not getattr(result, "edits", None):
            continue

        recipe_ids_applied.append(recipe_id)
        for edit in result.edits:
            all_edits.append(_edit_to_dict(edit, cell_index=cell_index))
        # Recipes are designed to compose; carry the rewritten source into
        # the next recipe in the chain.
        content = result.source

    return content, all_edits, recipe_ids_applied


def _resolve_output_lines(content: str, edits: list[dict]) -> None:
    """Populate ``output_line`` on each edit dict in place.

    ``src_line`` records the line number in the intermediate file content
    *when that recipe ran* — before the recipe's own comment prepending and
    before any later recipes in the alphabetical chain insert their own
    comments.  By the time the analyzer runs, every edit's code line has
    shifted forward by however many ``# SCOS*`` comment lines were inserted
    above it.

    This function scans the *final* output content (after all recipes have
    run) for each recipe's SCOS comment marker (``recipe_id + ":"``) and
    uses positional ordering (Nth marker occurrence → Nth edit sorted by
    src_line) to set the exact output line without any tolerance window.

    Edits whose marker is not found in the final content are left without
    ``output_line``; the analyzer falls back to ``src_line`` for those.
    """
    if not edits:
        return

    final_lines = content.splitlines()

    # Group edits by recipe_id; within each group sort by src_line so the
    # positional match below aligns with the file-order of the markers.
    by_recipe: dict[str, list[dict]] = {}
    for e in edits:
        by_recipe.setdefault(e["recipe_id"], []).append(e)
    for group in by_recipe.values():
        group.sort(key=lambda e: e.get("src_line") or 0)

    for recipe_id, group in by_recipe.items():
        marker = recipe_id + ":"
        code_lines: list[int] = []

        for idx, line in enumerate(final_lines, start=1):
            if marker not in line:
                continue
            if line.lstrip().startswith("#"):
                # Prepended standalone comment: advance past any additional
                # stacked SCOS comment lines to reach the code line.
                code_idx = idx + 1
                while (
                    code_idx <= len(final_lines)
                    and final_lines[code_idx - 1].lstrip().startswith("#")
                ):
                    code_idx += 1
                if code_idx <= len(final_lines):
                    code_lines.append(code_idx)
            else:
                # Inline comment on the code line itself.
                code_lines.append(idx)

        # Deduplicate consecutive identical code lines: multiple stacked SCOS
        # comments from the same recipe above the same statement each resolve
        # to the same code line, so collapse them to a single entry.
        deduped: list[int] = []
        for cl in code_lines:
            if not deduped or cl != deduped[-1]:
                deduped.append(cl)

        for edit, code_line in zip(group, deduped):
            edit["output_line"] = code_line


def apply_recipes_to_file(
    abs_path: str,
    rel_path: str,
    recipes: list[tuple[str, Any]],
    *,
    dry_run: bool,
    facts_db: str | None,
    strict: bool,
) -> tuple[list[dict], list[str], bool]:
    """Run every recipe over the file at ``abs_path``.

    Returns ``(edits, recipe_ids_applied, modified)`` where:
      * ``edits`` is the flat list of recipe_edits dicts to record.
      * ``recipe_ids_applied`` is the list of recipe IDs that produced at
        least one edit on this file.
      * ``modified`` is True iff at least one recipe changed the bytes.
    """
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        # The file is in the manifest but not in Output/ yet (e.g. odd
        # bootstrap). We do not own this; just skip.
        print(
            f"  SKIP {rel_path}: file not found at {abs_path}",
            file=sys.stderr,
        )
        return [], [], False

    original = content
    content, all_edits, recipe_ids_applied = _run_recipes_on_source(
        content, rel_path, recipes, facts_db=facts_db, strict=strict,
    )

    # Resolve the actual output line for each edit now that the full recipe
    # chain has run and the final content is known.  This must happen before
    # the file is written so the in-memory content matches what gets stored.
    _resolve_output_lines(content, all_edits)

    modified = content != original
    if modified and not dry_run:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)

    return all_edits, recipe_ids_applied, modified


def _recipe_notebook_scope(mod: Any) -> str:
    """Return a recipe's notebook scope: ``"cell"`` (default) or ``"module"``.

    ``"cell"`` recipes are local/expression rewrites (checkpoint→cache,
    map-subscript, master-drop, …) that are correct applied to one cell's
    source in isolation. ``"module"`` recipes reason about whole-file scope
    (e.g. "is ``spark`` defined anywhere?") and must see the concatenation of
    all Python cells, injecting once — never per cell.
    """
    scope = getattr(mod, "NOTEBOOK_SCOPE", "cell")
    return scope if scope in ("cell", "module") else "cell"


def _splice_single_insertion_into_cell(
    py_cells: list[Any], concat: str, new_concat: str, sep: str
) -> bool:
    """Map a single contiguous insertion in ``new_concat`` back into its cell.

    Module-scope recipes (e.g. the ambient-``spark`` bootstrap) inject one
    contiguous block into the notebook's logical module. That block is not
    necessarily at byte 0 — the ambient-``spark`` recipe inserts it *after* the
    module's leading header comments (``module.header``), so it is an interior
    insertion, not a pure top-of-file prepend.

    This maps the change back to the notebook cells by diffing ``concat`` and
    ``new_concat``: it computes the common prefix/suffix, confirms the ONLY
    change is a single inserted block (removing that block reproduces ``concat``
    exactly — no edits or deletions elsewhere), then splices the block into the
    Python cell that owns the insertion offset, mutating that cell's ``source``
    in place. A pure top-of-module prepend is just the offset-0 special case.

    Returns ``True`` when a clean single insertion was spliced; ``False`` when
    the diff is not a single pure insertion (a real modification/deletion), in
    which case the caller leaves the notebook untouched.
    """
    if new_concat == concat:
        return False
    # Empty original module: the whole rewrite is the insertion.
    if not concat:
        py_cells[0].source = new_concat + py_cells[0].source
        return True

    n_old, n_new = len(concat), len(new_concat)
    if n_new <= n_old:
        # A pure insertion strictly grows the text; anything else is an
        # edit/deletion we refuse to remap onto cell boundaries.
        return False

    # Longest common prefix.
    p = 0
    max_p = min(n_old, n_new)
    while p < max_p and concat[p] == new_concat[p]:
        p += 1
    # Longest common suffix that does not overlap the prefix on either side.
    s = 0
    while (
        s < (n_old - p)
        and s < (n_new - p)
        and concat[n_old - 1 - s] == new_concat[n_new - 1 - s]
    ):
        s += 1

    # Pure single insertion iff the prefix + suffix cover ALL of the original
    # (i.e. removing the middle chunk of ``new_concat`` reproduces ``concat``).
    if new_concat[:p] + new_concat[n_new - s:] != concat:
        return False
    inserted = new_concat[p : n_new - s]
    if not inserted:
        return False

    # Map the insertion offset ``p`` (into ``concat``) to (cell, intra-offset).
    # ``concat`` is ``sep.join(cell.source ...)``; a single ``sep`` sits between
    # consecutive cells. An offset within [start, start+len(source)] belongs to
    # that cell (inclusive of its end, before the following separator).
    start = 0
    for cell in py_cells:
        length = len(cell.source)
        if p <= start + length:
            intra = p - start
            cell.source = cell.source[:intra] + inserted + cell.source[intra:]
            return True
        start += length + len(sep)
    # Offset past every cell (shouldn't happen for an insertion) → append to last.
    py_cells[-1].source += inserted
    return True


def _apply_module_recipe_to_notebook(
    nb: Any,
    py_cells: list[Any],
    rel_path: str,
    recipe_id: str,
    mod: Any,
    *,
    facts_db: str | None,
    strict: bool,
) -> tuple[list[dict], bool]:
    """Apply one ``NOTEBOOK_SCOPE == "module"`` recipe to a notebook.

    The recipe is evaluated against the concatenation of all Python code cells
    (the notebook's logical module). These recipes inject a single contiguous
    block (e.g. the ambient-``spark`` bootstrap), which the ambient-``spark``
    recipe places *after* the module's leading header comments — an interior
    insertion, not necessarily a top-of-file prepend. The rewrite is mapped back
    by diffing the concatenation and splicing the inserted block into the cell
    that owns it (see :func:`_splice_single_insertion_into_cell`). If the recipe
    makes anything other than a single clean insertion (an edit or deletion), it
    is skipped for the notebook with a warning — we never risk corrupting cell
    boundaries.

    Returns ``(edits, modified)``.
    """
    if not py_cells:
        return [], False

    sep = "\n"
    concat = sep.join(c.source for c in py_cells)
    new_concat, edits, applied = _run_recipes_on_source(
        concat, rel_path, [(recipe_id, mod)],
        facts_db=facts_db, strict=strict, cell_index=py_cells[0].index,
    )
    if not applied or new_concat == concat:
        return [], False

    if not _splice_single_insertion_into_cell(py_cells, concat, new_concat, sep):
        print(
            f"  WARN {rel_path}: module-scope recipe {recipe_id!r} did not make a "
            "single-insertion change to the notebook; skipped to protect cell "
            "boundaries.",
            file=sys.stderr,
        )
        return [], False

    return edits, True


def apply_recipes_to_notebook(
    abs_path: str,
    rel_path: str,
    recipes: list[tuple[str, Any]],
    *,
    dry_run: bool,
    facts_db: str | None,
    strict: bool,
) -> tuple[list[dict], list[str], bool]:
    """Run every recipe over a notebook in place, scope-aware.

    Preserves the notebook's native on-disk format via ``notebook_io``.

    * ``NOTEBOOK_SCOPE == "cell"`` recipes (the default) run per Python code
      cell — correct for local/expression rewrites.
    * ``NOTEBOOK_SCOPE == "module"`` recipes run once against the concatenation
      of all Python cells and inject into the first cell — correct for
      whole-file reasoning (e.g. ambient-``spark`` bootstrap injection).

    Each recorded edit carries a ``cell_index`` so its (cell-relative)
    ``src_line`` is a navigable locator. Non-Python cells and magic-prefixed
    Python cells that won't parse are left byte-identical.

    Returns ``(edits, recipe_ids_applied, modified)`` with the same contract
    as :func:`apply_recipes_to_file`.
    """
    try:
        nb = parse_notebook(abs_path)
    except FileNotFoundError:
        print(f"  SKIP {rel_path}: file not found at {abs_path}", file=sys.stderr)
        return [], [], False
    except Exception as exc:  # noqa: BLE001
        print(
            f"  SKIP {rel_path}: could not parse notebook "
            f"({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        if strict:
            raise
        return [], [], False

    cell_recipes = [(rid, m) for rid, m in recipes
                    if _recipe_notebook_scope(m) == "cell"]
    module_recipes = [(rid, m) for rid, m in recipes
                      if _recipe_notebook_scope(m) == "module"]

    all_edits: list[dict] = []
    recipe_ids_applied: list[str] = []
    modified = False

    # 1. Per-cell (local) recipes.
    for cell in nb.cells:
        if cell.cell_type != "code" or cell.cell_language != "python":
            continue
        # Guard: skip cells that are not parseable Python (e.g. a leading
        # IPython magic / shell escape). Leaving them byte-identical avoids
        # noisy per-recipe parse errors and never corrupts a cell.
        if not _is_parseable_python(cell.source):
            continue

        original_cell = cell.source
        new_source, cell_edits, applied = _run_recipes_on_source(
            cell.source, rel_path, cell_recipes,
            facts_db=facts_db, strict=strict, cell_index=cell.index,
        )
        if new_source != original_cell:
            cell.source = new_source
            modified = True
        all_edits.extend(cell_edits)
        recipe_ids_applied.extend(applied)

    # 2. Module-scope recipes: evaluate against the concatenation of parseable
    #    Python cells, inject once into the first such cell.
    py_cells = [
        c for c in nb.cells
        if c.cell_type == "code" and c.cell_language == "python"
        and _is_parseable_python(c.source)
    ]
    for recipe_id, mod in module_recipes:
        mod_edits, mod_modified = _apply_module_recipe_to_notebook(
            nb, py_cells, rel_path, recipe_id, mod,
            facts_db=facts_db, strict=strict,
        )
        if mod_modified:
            modified = True
            recipe_ids_applied.append(recipe_id)
            all_edits.extend(mod_edits)

    if modified and not dry_run:
        write_notebook(abs_path, nb)

    return all_edits, recipe_ids_applied, modified


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 0.5: apply LibCST recipes deterministically to every "
            "manifest file before the LLM analyzer runs."
        )
    )
    parser.add_argument(
        "--state", required=True, help="Path to migration_state.json"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be rewritten without modifying any files.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Fail the phase on the first per-file recipe error. Default is "
            "best-effort: log and continue."
        ),
    )
    parser.add_argument(
        "--facts-db",
        default=os.environ.get("SCOS_FACTS_DB"),
        help=(
            "Optional path to a sqlite facts.db; recipes will additionally "
            "log every edit to the recipe_edits table."
        ),
    )
    args = parser.parse_args()

    state_path = os.path.abspath(args.state)
    if not os.path.exists(state_path):
        print(
            f"ERROR: migration_state.json not found: {state_path}",
            file=sys.stderr,
        )
        return 1

    state = load_state(state_path)
    manifest: list[str] = state.get("manifest", [])
    migrated_dir: str = state.get("migrated_dir", "")
    if not manifest:
        print(
            "ERROR: manifest is empty in migration_state.json",
            file=sys.stderr,
        )
        return 1
    if not migrated_dir:
        print(
            "ERROR: migrated_dir not set in migration_state.json",
            file=sys.stderr,
        )
        return 1

    recipes = discover_recipes()

    print("=" * 60)
    print("PHASE 0.5: DETERMINISTIC PRE-PROCESSING")
    print("=" * 60)
    print(f"  State        : {state_path}")
    print(f"  Migrated dir : {migrated_dir}")
    print(f"  Manifest     : {len(manifest)} file(s)")

    # Pre-flight (Phase 0.5.0): detect and safely auto-fix pre-existing Python
    # syntax errors in the source BEFORE recipes run. Recipes parse with
    # ``cst.parse_module`` and silently skip un-parseable units, so a
    # pre-existing syntax error (e.g. a stray-indented notebook cell) would
    # otherwise survive untouched into Phase 2 and trap the fixer's compile
    # guard in a revert loop. Records ``preexisting_syntax`` into the state.
    pre = run_precompile_check(state, dry_run=args.dry_run)
    if pre["preexisting_errors"]:
        print(
            f"  Pre-flight   : {pre['preexisting_errors']} pre-existing syntax "
            f"error(s) — {pre['auto_fixed']} auto-fixed, "
            f"{pre['unresolved']} unresolved"
        )
    else:
        print("  Pre-flight   : no pre-existing syntax errors")

    print(
        f"  Recipes      : {len(recipes)} "
        f"({', '.join(rid for rid, _ in recipes) if recipes else 'none'})"
    )
    print(f"  Mode         : {'dry-run' if args.dry_run else 'apply'}"
          f"{' [strict]' if args.strict else ''}")
    if args.facts_db:
        print(f"  Facts DB     : {args.facts_db}")
    print()

    if not recipes:
        # Phase is still considered passed when there are no recipes
        # registered — there's nothing to do, and downstream phases must
        # not gate on this. Still record the run for auditability.
        _record_completion(
            state, recipes_run=[], files_processed=0,
            files_modified=0, total_edits=0,
        )
        if not args.dry_run:
            save_state(state_path, state)
        print("No recipes registered; nothing to do.")
        return 0

    recipe_edits_block: dict[str, list[dict]] = {}
    files_processed = 0
    files_modified = 0
    total_edits = 0
    recipes_used: set[str] = set()

    for entry in manifest:
        abs_path = resolve_file_path(entry, migrated_dir)

        # Route by kind: notebooks (any format notebook_io recognises) run the
        # recipe chain per Python code cell; plain .py files run over the whole
        # file. Databricks-exported .py files end in .py but ARE notebooks, so
        # the notebook check must come first.
        is_nb = _is_notebook_entry(abs_path)
        if not is_nb and not entry.endswith(".py"):
            # Not a notebook and not a .py source — nothing recipes target.
            continue

        files_processed += 1

        try:
            if is_nb:
                edits, applied, modified = apply_recipes_to_notebook(
                    abs_path,
                    entry,
                    recipes,
                    dry_run=args.dry_run,
                    facts_db=args.facts_db,
                    strict=args.strict,
                )
            else:
                edits, applied, modified = apply_recipes_to_file(
                    abs_path,
                    entry,
                    recipes,
                    dry_run=args.dry_run,
                    facts_db=args.facts_db,
                    strict=args.strict,
                )
        except Exception as exc:  # only reachable in --strict
            print(
                f"FATAL: --strict abort on {entry}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            traceback.print_exc()
            return 2

        if modified:
            files_modified += 1
        if edits:
            recipe_edits_block[entry] = edits
            total_edits += len(edits)
            recipes_used.update(applied)
            print(
                f"  {'[dry-run] ' if args.dry_run else ''}edited "
                f"{entry}  (+{len(edits)} edit(s) by "
                f"{', '.join(applied)})"
            )

    # Persist the audit trail and phase completion. We intentionally
    # overwrite ``recipe_edits`` rather than merging — Phase 0.5 is
    # idempotent and re-runs are safe (recipes are no-ops on already-
    # rewritten code), so the latest run is the source of truth.
    state["recipe_edits"] = recipe_edits_block
    _record_completion(
        state,
        recipes_run=sorted(recipes_used),
        files_processed=files_processed,
        files_modified=files_modified,
        total_edits=total_edits,
    )

    if not args.dry_run:
        save_state(state_path, state)

    print()
    print("=" * 60)
    print("PHASE 0.5 SUMMARY")
    print("=" * 60)
    print(f"  Files processed : {files_processed}")
    print(f"  Files modified  : {files_modified}")
    print(f"  Total edits     : {total_edits}")
    print(f"  Recipes used    : {', '.join(sorted(recipes_used)) or '(none triggered)'}")
    print(f"  State updated   : {'no (dry-run)' if args.dry_run else 'yes'}")

    return 0


def _record_completion(
    state: dict,
    *,
    recipes_run: list[str],
    files_processed: int,
    files_modified: int,
    total_edits: int,
) -> None:
    """Mirror the ``phases_completed`` write contract used by every other
    phase, so ``validate_migration_state.py`` sees Phase 0.5 evidence
    without changes."""
    phases = state.setdefault("phases_completed", {})
    phases["0_5_preprocess"] = {
        "status": "passed",
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files_processed": files_processed,
        "files_modified": files_modified,
        "total_edits": total_edits,
        "recipes_run": recipes_run,
    }


if __name__ == "__main__":
    sys.exit(main())
