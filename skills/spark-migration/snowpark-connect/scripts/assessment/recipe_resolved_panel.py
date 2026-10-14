"""Build the standalone 'auto-resolved' panel for the report.

This module is the SOLE consumer of ``migration_state.json[recipe_edits]``
in the report-rendering pipeline. It exists as its own file (rather than
folded into ``transform_analysis`` or the IR merge) so the Recipe-Data
Isolation Guarantee from the Tier-B plan is statically enforceable: a
``grep recipe_edits scripts/assessment/{transform_analysis,scan_codebase,
assess_ir}.py`` returns nothing.

Risk / score / compatibility / readiness fields are deliberately NOT
touched here. The panel rows are pure identity (file, line, recipe id,
classification) sourced verbatim from ``recipe_edits``, optionally
enriched with a human-readable description scraped from each recipe's
module docstring at render time (scalable: any new recipe added under
``scripts/recipes/`` gets a description for free).
"""
from __future__ import annotations

import ast
import difflib
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Mapping, NamedTuple, Sequence

# Re-export the IR row type from a single source of truth.
from assess_ir import RecipeKind, RecipeResolvedRow  # noqa: E402

logger = logging.getLogger(__name__)


def _classify_recipe_kind(recipe_id: str) -> RecipeKind:
    """Return ``'rewrite'`` / ``'annotate'`` / ``'comment'`` / ``'other'``.

    Mirrors :func:`analyze_pyspark._classify_recipe_kind` exactly. The split
    is by recipe-folder suffix convention — every ``scripts/recipes/<name>/``
    is named with one of these suffixes per the project's recipe-author
    contract.
    """
    if recipe_id.endswith("_rewrite"):
        return "rewrite"
    if recipe_id.endswith("_annotate"):
        return "annotate"
    if recipe_id.endswith("_comment"):
        return "comment"
    return "other"


# ---------------------------------------------------------------------------
# Recipe description extraction
# ---------------------------------------------------------------------------
#
# Description text comes from each recipe's module-level docstring's first
# paragraph. This is scalable: a new ``scripts/recipes/<id>/recipe.py``
# automatically gets a description in the panel with zero code change here,
# as long as its docstring opens with a one-sentence summary (which every
# current recipe does, and the project's recipe-author contract requires).
#
# We do NOT hardcode a recipe-id-to-description table. We do NOT call an LLM
# at render time (the panel is informational; recipe authors already wrote
# the human-readable summary in their docstring). If a recipe's docstring is
# missing or malformed, the template falls back to showing the recipe_id.

_RST_INLINE_LITERAL = re.compile(r"``(.+?)``", re.DOTALL)
"""Matches RST `` ``foo`` `` inline-literal markup. We strip the backticks
so the description renders as plain prose. Keeping the content (just
dropping the markup) preserves the API names the docstring is calling
out — those are the most useful tokens for a stakeholder skim."""

_RST_ROLE = re.compile(r":[a-zA-Z]+:`(.+?)`")
"""Strips Sphinx role markers like ``:func:`foo``` → ``foo``."""

_WHITESPACE = re.compile(r"\s+")


def _clean_rst_paragraph(paragraph: str) -> str:
    """Strip the common RST markup we see in recipe docstrings.

    Currently handles ``inline literals`` and ``:role:`text``` roles. We
    intentionally leave content (the strings inside the markup) so API
    names survive. Whitespace is collapsed to single spaces so the
    paragraph fits cleanly on one line in the panel.
    """
    out = _RST_ROLE.sub(r"\1", paragraph)
    out = _RST_INLINE_LITERAL.sub(r"\1", out)
    return _WHITESPACE.sub(" ", out).strip()


def _extract_first_paragraph(docstring: str | None) -> str | None:
    """Return the first paragraph of a docstring, cleaned to plain text.

    A "paragraph" ends at the first blank line. Returns ``None`` for
    empty / whitespace-only docstrings.
    """
    if not docstring or not docstring.strip():
        return None
    para = docstring.strip().split("\n\n", 1)[0]
    cleaned = _clean_rst_paragraph(para)
    return cleaned or None


@lru_cache(maxsize=None)
def _read_recipe_summary(recipe_dir: Path, recipe_id: str) -> str | None:
    """Read ``<recipe_dir>/<recipe_id>/recipe.py`` and return its
    docstring's first paragraph.

    ``lru_cache``-d on the (dir, id) pair so a workload with multiple
    edits from the same recipe only pays the AST-parse cost once. Cache
    keys are :class:`Path` instances; tests that need a fresh cache can
    call ``_read_recipe_summary.cache_clear()``.

    Returns ``None`` on any failure (missing file, parse error, missing
    docstring) — the template falls back to displaying just the recipe
    id in that case, so a single broken recipe never breaks the report.
    """
    recipe_file = recipe_dir / recipe_id / "recipe.py"
    try:
        source = recipe_file.read_text(encoding="utf-8")
    except OSError as e:
        logger.debug("Could not read recipe file %s: %s", recipe_file, e)
        return None
    try:
        module = ast.parse(source)
    except SyntaxError as e:
        logger.warning("Could not parse %s as Python: %s", recipe_file, e)
        return None
    docstring = ast.get_docstring(module, clean=True)
    return _extract_first_paragraph(docstring)


def get_recipe_summary(
    recipe_id: str, recipes_dir: Path | None
) -> str | None:
    """Public-facing helper: return a one-paragraph summary for ``recipe_id``.

    Returns ``None`` when ``recipes_dir`` is not provided (e.g. tests that
    only care about the row identity) or when the recipe folder / docstring
    is missing. The caller / template treats a None summary as "fall back
    to recipe_id".
    """
    if recipes_dir is None:
        return None
    if not recipe_id:
        return None
    return _read_recipe_summary(recipes_dir, recipe_id)


# ---------------------------------------------------------------------------
# Source-snippet extraction (from the materialized original source tree)
# ---------------------------------------------------------------------------

# Soft cap on snippet height. A recipe whose ``src_line`` lands on a
# ``def`` / ``class`` statement would otherwise dump the whole function
# body; 30 lines matches the longest snippets the LLM analyzer emits
# into ``analysis.json`` Per-File Compatibility cards, so the visual
# weight of the auto-resolved card matches an analyzer card.
_MAX_SNIPPET_LINES = 30


@lru_cache(maxsize=None)
def _read_source_lines(file_path: Path) -> tuple[str, ...]:
    """Read a source file and cache its lines for repeat lookups within a
    single render. Returns an empty tuple on read failure."""
    try:
        return tuple(file_path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError as e:
        logger.debug("Could not read source file %s: %s", file_path, e)
        return ()


@lru_cache(maxsize=None)
def _parse_source_ast(file_path: Path) -> ast.AST | None:
    """Parse a Python source file once per render. ``None`` when parsing
    fails (legitimately bad source; non-Python file accidentally listed
    in ``recipe_edits``). Cached because a single file may appear in
    multiple ``recipe_edits`` entries."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("Could not read source file %s for AST parse: %s", file_path, e)
        return None
    try:
        return ast.parse(text, filename=str(file_path))
    except SyntaxError as e:
        # Original-source files come from a committed git tag, so syntax
        # errors here would be surprising — but we tolerate them rather
        # than blocking the whole panel.
        logger.debug("Could not AST-parse %s: %s", file_path, e)
        return None


def _resolve_source_file(rel_file_path: str, source_dir: Path) -> Path | None:
    """Map a ``recipe_edits`` file key to a concrete file under
    ``source_dir``. Mirrors transform_analysis's basename fallback so
    shallow workloads (where the recipe key has no subdirs) still
    resolve."""
    if not rel_file_path:
        return None
    direct = source_dir / rel_file_path
    if direct.is_file():
        return direct
    fallback = source_dir / Path(rel_file_path).name
    if fallback.is_file():
        return fallback
    return None


def _find_enclosing_stmt_span(
    tree: ast.AST, src_line: int
) -> tuple[int, int] | None:
    """Return ``(start_line, end_line)`` of the innermost ``ast.stmt``
    whose 1-indexed line range contains ``src_line``.

    Why innermost: for an assignment inside an ``if``-body, both nodes
    contain ``src_line`` — but the user wants to see the assignment,
    not the entire branch. Innermost = tightest span.

    Returns ``None`` when no statement matches (e.g. ``src_line`` is
    blank/comment-only and Python's AST doesn't model it).
    """
    best: tuple[int, int] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None:
            continue
        if not (start <= src_line <= end):
            continue
        span = end - start
        if best is None or span < (best[1] - best[0]):
            best = (start, end)
    return best


def get_source_snippet(
    rel_file_path: str,
    line: int,
    source_dir: Path | None,
    min_start_line: int | None = None,
) -> tuple[str, int]:
    """Return ``(code_block, end_line)`` for the statement at ``line``.

    ``code_block`` is the raw multi-line text of the smallest enclosing
    ``ast.stmt`` containing ``line``, preserving original indentation so
    the ``<pre>`` block renders like real code. This is the deterministic
    counterpart of the LLM-picked ``code`` field in ``analysis.json``
    Per-File Compatibility cards — same UX, no model.

    ``end_line`` is the last line of that statement (1-indexed). When
    equal to ``line``, the statement is single-line.

    ``min_start_line`` optionally lower-bounds the returned start. Used
    by the marker-driven panel path to include the SCOS comment line(s)
    that sit immediately above the targeted statement — so the rendered
    snippet shows ``# SCOS-WARN: ...`` *together with* the line it
    describes, instead of just the bare line. When ``None`` (default),
    the AST span is used as-is.

    Falls back gracefully:
      * ``source_dir is None`` / file missing / file unparseable
        → ``("", 0)`` (template hides the ``<pre>`` block).
      * AST has no stmt at ``line`` (blank / comment-only line)
        → single-line read, ``end_line = line``.
      * Block span exceeds ``_MAX_SNIPPET_LINES``
        → truncated to first ``_MAX_SNIPPET_LINES - 1`` lines plus a
          marker line, so the card stays readable.
    """
    if source_dir is None or line < 1:
        return ("", 0)
    file_path = _resolve_source_file(rel_file_path, source_dir)
    if file_path is None:
        return ("", 0)
    lines = _read_source_lines(file_path)
    if not lines or line > len(lines):
        return ("", 0)

    # Try AST first for multi-line span; fall back to single-line.
    start, end = line, line
    tree = _parse_source_ast(file_path)
    if tree is not None:
        span = _find_enclosing_stmt_span(tree, line)
        if span is not None:
            start, end = span

    # Marker-context extension: pull the start earlier when the caller
    # wants preceding lines (typically a SCOS marker comment) included
    # in the same snippet.
    if min_start_line is not None and 1 <= min_start_line < start:
        start = min_start_line

    # Snippet height cap — see comment on ``_MAX_SNIPPET_LINES``.
    block_lines = list(lines[start - 1 : end])
    if len(block_lines) > _MAX_SNIPPET_LINES:
        kept = block_lines[: _MAX_SNIPPET_LINES - 1]
        omitted = len(block_lines) - len(kept)
        block_lines = kept + [f"# ... ({omitted} more lines)"]
        end = start + len(block_lines) - 1

    # Strip common leading indent so the snippet renders flush in its
    # card; preserves relative indentation of nested lines. Mirrors how
    # the LLM-emitted ``f.code`` in analysis.json is already de-indented.
    indents = [
        len(ln) - len(ln.lstrip(" "))
        for ln in block_lines
        if ln.strip()  # ignore blank lines when computing indent
    ]
    common = min(indents) if indents else 0
    if common:
        block_lines = [ln[common:] if len(ln) >= common else ln for ln in block_lines]

    return ("\n".join(block_lines), end)


# ---------------------------------------------------------------------------
# SCOS marker scanning (the ground-truth source for what each recipe did
# at each site — markers are inserted by the recipe at edit time)
# ---------------------------------------------------------------------------

# Markers look like:
#   # SCOS-WARN: <recipe_id>: <message>
#   # SCOS-TODO: [<CODE>] <recipe_id>: <message>
# Both forms appear standalone on their own line AND as trailing comments
# after a rewritten statement (e.g. ``pass  # SCOS-WARN: ...``). The
# regex tolerates leading code via ``.*?`` before the ``#``.
_SCOS_MARKER_RE = re.compile(
    r"#\s*SCOS-(?P<type>[A-Z]+):\s*"
    r"(?:\[(?P<code>[A-Z0-9]+)\]\s*)?"
    r"(?P<recipe>[a-z][a-z0-9_]+):\s*"
    r"(?P<msg>.+)$"
)


class _ScosMarker(NamedTuple):
    """One SCOS marker comment found in a post-recipe file."""

    line: int          # 1-indexed post-recipe line containing the marker
    recipe_id: str
    marker_type: str   # "WARN" / "TODO" / "FIX" / ...
    message: str       # the per-instance text after the recipe id
    is_trailing: bool  # True when the marker shares its line with code
                       # (e.g. ``pass  # SCOS-WARN: ...``); False when the
                       # marker is a standalone comment-only line


@lru_cache(maxsize=None)
def _scan_scos_markers(post_file_path: Path) -> tuple[_ScosMarker, ...]:
    """Scan a post-recipe Python file for SCOS markers. Cached so the
    same file isn't reparsed for each ``recipe_edits`` entry that lives
    in it. Returns an empty tuple on read failure (mirrors
    ``_read_source_lines``)."""
    try:
        text = post_file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("Could not read post-recipe file %s for markers: %s", post_file_path, e)
        return ()
    out: list[_ScosMarker] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        m = _SCOS_MARKER_RE.search(raw)
        if m is None:
            continue
        # Trailing if there's any non-whitespace before the '#' of the marker.
        hash_idx = raw.find("#", 0, m.start())
        # The marker regex starts at the actual '#' position; the comment
        # is "trailing" if there's non-blank source content on the same
        # line before that '#'.
        prefix = raw[: m.start()].rstrip()
        is_trailing = bool(prefix) and not prefix.endswith("#")
        out.append(
            _ScosMarker(
                line=i,
                recipe_id=m.group("recipe"),
                marker_type=m.group("type"),
                message=(m.group("msg") or "").strip(),
                is_trailing=is_trailing,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Diff-opcode based outcome classification
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _diff_opcodes(original_path: Path, post_path: Path) -> tuple[tuple, ...]:
    """Compute SequenceMatcher opcodes between original and post-recipe
    versions of a file. Cached because every edit in the file reuses
    the same diff. Returns an empty tuple if either read fails."""
    orig_lines = _read_source_lines(original_path)
    post_lines = _read_source_lines(post_path)
    if not orig_lines or not post_lines:
        return ()
    matcher = difflib.SequenceMatcher(a=orig_lines, b=post_lines, autojunk=False)
    return tuple(matcher.get_opcodes())


def _classify_at_post_line(opcodes: Sequence[tuple], post_line: int) -> RecipeKind:
    """Classify the recipe's outcome at a given POST-recipe line.

    * ``equal`` opcode → ``annotate`` (line preserved from original; the
      recipe only added neighboring context like a comment marker)
    * ``insert`` / ``replace`` opcode → ``rewrite`` (line is new in post
      or replaces an original line; the recipe actually transformed code)
    * ``delete`` opcode → ``comment`` (only applies when the marker
      points at a line that was removed; rare)
    * No opcode hit → ``other`` (out-of-range; defensive)
    """
    idx = post_line - 1
    for tag, _i1, _i2, j1, j2 in opcodes:
        if tag in ("equal", "insert", "replace") and j1 <= idx < j2:
            if tag == "equal":
                return "annotate"
            return "rewrite"
    return "other"


def _post_line_to_original(
    opcodes: Sequence[tuple], post_line: int
) -> int | None:
    """Map a post-recipe line (1-indexed) to its original-source line.

    Mirrors the prefix-only ``replace`` semantics of
    ``transform_analysis._build_post_to_original_line_map`` exactly:

    * ``equal`` opcode → 1:1 by positional offset
    * ``replace`` opcode → 1:1 over the ``min(orig_span, post_span)``
      prefix, ``None`` for the widened tail
    * ``insert`` opcode → ``None`` (wholly new in post)

    Returns ``None`` when the post line has no faithful original
    equivalent; the panel builder treats that as a fallback signal and
    flips the row's ``coord_system`` to ``"post"``.
    """
    post_idx = post_line - 1
    for op, i1, i2, j1, j2 in opcodes:
        if not (j1 <= post_idx < j2):
            continue
        if op == "equal":
            return i1 + (post_idx - j1) + 1
        if op == "replace":
            offset = post_idx - j1
            prefix = min(i2 - i1, j2 - j1)
            if offset < prefix:
                return i1 + offset + 1
            return None  # widened tail — no faithful pre-image
        # ``insert`` → no original equivalent.
        return None
    return None


def _classify_at_original_line(
    opcodes: Sequence[tuple], original_line: int
) -> RecipeKind:
    """Classify the recipe's outcome at a given ORIGINAL-source line.
    Used by the silent-rewrite fallback path when no marker is found.

    * ``equal`` → ``annotate``
    * ``replace`` / ``delete`` → ``rewrite``
    * ``insert`` → never matches by original-index (insert has empty
      original span); fall through to ``other``
    """
    idx = original_line - 1
    for tag, i1, i2, _j1, _j2 in opcodes:
        if i1 <= idx < i2:
            if tag == "equal":
                return "annotate"
            if tag in ("replace", "delete"):
                return "rewrite"
    return "other"


# ---------------------------------------------------------------------------
# Panel builder
# ---------------------------------------------------------------------------


def build_recipe_resolved_panel(
    recipe_edits: Mapping[str, Sequence[Mapping[str, object]]] | None,
    recipes_dir: Path | None = None,
    original_source_dir: Path | None = None,
    post_recipe_source_dir: Path | None = None,
) -> list[RecipeResolvedRow]:
    """Build the auto-resolved panel rows.

    Two data sources, used in priority order:

    1. **SCOS markers in the post-recipe file** (preferred when both
       source dirs are wired). The recipe inserts a marker comment at
       the actual site it acted on; the comment carries a per-instance
       message that's strictly more useful than the recipe's generic
       docstring (it names the specific call / variable / reason). The
       marker line is also accurate — unlike ``recipe_edits[*].src_line``
       which has been observed off by ±2 lines.

    2. **``recipe_edits`` ledger** (fallback for silent rewrites — recipes
       that change code without inserting a marker, e.g.
       ``map_column_subscript_colkey_to_element_at_rewrite``). For these
       we read the snippet from the original source at ``src_line`` and
       use the generic docstring for the message.

    Either way, ``kind`` is computed from the diff between original and
    post-recipe (never trusted from the recipe-id suffix), so a
    ``*_rewrite`` recipe that gave up and only added a TODO marker is
    correctly labelled ``annotate`` instead of misleadingly ``rewrite``.

    Optional enrichments:

    * ``recipes_dir`` — used to extract the generic docstring summary
      for the silent-rewrite fallback path only.
    * ``original_source_dir`` — required for diff classification AND for
      reading snippets in the fallback path.
    * ``post_recipe_source_dir`` — required for the marker-driven path.
      When None, every row falls into the silent-rewrite fallback.

    Returns an empty list when ``recipe_edits`` is falsy. The template
    hides the panel entirely in that case.

    Recipe-Data Isolation Guarantee: this function reads only
    ``recipe_edits``, the recipe-folder docstrings, the original-source
    files, and the post-recipe source files (for marker scanning and
    diff classification). It never touches any analyzer finding,
    scanner output, or other IR field. The caller
    (``render_assessment.build_assessment``) assigns the returned list
    to ``Assessment.recipe_resolved`` AFTER the IR merge completes, so
    recipe data never participates in any risk/score/compatibility
    aggregation.
    """
    if not recipe_edits:
        return []

    rows: list[RecipeResolvedRow] = []
    for file_path, edits in recipe_edits.items():
        if not isinstance(file_path, str) or not edits:
            continue

        # Resolve concrete file paths once per file.
        orig_path = (
            _resolve_source_file(file_path, original_source_dir)
            if original_source_dir is not None
            else None
        )
        post_path = (
            _resolve_source_file(file_path, post_recipe_source_dir)
            if post_recipe_source_dir is not None
            else None
        )

        # Marker-driven path active only when BOTH source trees are
        # available — we need post for the markers and original for the
        # diff classification.
        markers_by_recipe: dict[str, list[_ScosMarker]] = {}
        opcodes: tuple = ()
        if orig_path is not None and post_path is not None:
            opcodes = _diff_opcodes(orig_path, post_path)
            for m in _scan_scos_markers(post_path):
                markers_by_recipe.setdefault(m.recipe_id, []).append(m)
            # Stable order so the "Nth edit ↔ Nth marker" pairing is
            # deterministic.
            for ms in markers_by_recipe.values():
                ms.sort(key=lambda m: m.line)

        # Track which markers we've consumed so we can pop them in
        # arrival order without re-iterating.
        consumed: dict[str, int] = {}

        # Iterate edits in src_line order for deterministic pairing
        # against the line-sorted markers.
        edits_sorted = [
            e for e in edits
            if isinstance(e, Mapping)
            and isinstance(e.get("recipe_id"), str)
            and e["recipe_id"]  # reject blank ids — mirrors the
                                # pre-refactor contract of skipping
                                # malformed ledger entries silently.
            and isinstance(e.get("src_line"), int)
        ]
        edits_sorted.sort(key=lambda e: (e["src_line"], e["recipe_id"]))

        for edit in edits_sorted:
            recipe_id = edit["recipe_id"]
            src_line = edit["src_line"]

            marker = None
            if recipe_id in markers_by_recipe:
                idx = consumed.get(recipe_id, 0)
                if idx < len(markers_by_recipe[recipe_id]):
                    marker = markers_by_recipe[recipe_id][idx]
                    consumed[recipe_id] = idx + 1

            if marker is not None and post_path is not None:
                # Marker-driven row: per-instance message + snippet pulled
                # from the post-recipe file (where the marker lives).
                # Target line is the marker's own line for trailing
                # comments (the comment shares the line with the code it
                # describes), otherwise the line immediately below the
                # standalone marker comment.
                target_post = (
                    marker.line if marker.is_trailing else marker.line + 1
                )
                # For a standalone marker, extend the snippet upward to
                # include the marker comment itself so the rendered card
                # shows "# SCOS-WARN: ..." right above the line it
                # describes — the same context the user would see when
                # opening the post-recipe file. Trailing markers already
                # live on the same line as the code, so no extension.
                min_start = None if marker.is_trailing else marker.line
                snippet, end_post = get_source_snippet(
                    file_path,
                    target_post,
                    post_recipe_source_dir,
                    min_start_line=min_start,
                )
                # Rebase the CODE line range (excluding the marker line
                # which is recipe-introduced and has no original
                # equivalent) to original-source coords. This keeps the
                # auto-resolved panel's line numbers aligned with the
                # rest of the report (Issue Summary / Per-File
                # Compatibility), so the user has a single mental model
                # for "what file:line am I being pointed at."
                #
                # Two cases fall back to post coords with a flag:
                #   1. Trailing marker on a wholly-new line (e.g.
                #      ``pass  # SCOS-WARN: ...`` that replaced a
                #      chained ``.config()`` — the pass line is itself
                #      recipe-introduced).
                #   2. Widened ``replace`` opcode where the target post
                #      line is in the tail with no faithful 1:1 pre-image.
                # In both cases the row's ``coord_system`` flips to
                # "post" and the template surfaces a small annotation
                # next to the line number.
                orig_start = (
                    _post_line_to_original(opcodes, target_post)
                    if opcodes else None
                )
                orig_end = (
                    _post_line_to_original(opcodes, end_post)
                    if opcodes else None
                )
                if orig_start is not None and orig_end is not None:
                    display_line = orig_start
                    display_end = orig_end
                    coord_system = "original"
                else:
                    # Fallback: keep post coords for this one row so the
                    # user can still navigate, but tell them via the flag.
                    display_line = target_post
                    display_end = end_post
                    coord_system = "post"
                # Kind classification still uses the CODE line (target),
                # not the marker line — the marker line is always an
                # ``insert`` opcode (newly added by the recipe), which
                # would mis-classify everything as ``rewrite``.
                kind = (
                    _classify_at_post_line(opcodes, target_post)
                    if opcodes else "other"
                )
                rows.append(
                    RecipeResolvedRow(
                        file=file_path,
                        line=display_line,
                        end_line=display_end,
                        code=snippet,
                        recipe_id=recipe_id,
                        kind=kind,
                        message=marker.message,
                        coord_system=coord_system,
                    )
                )
                continue

            # Silent-rewrite fallback: no SCOS marker for this edit. Use
            # the recipe-edits ledger as-is, read snippet from original,
            # classify via diff at src_line if opcodes are available.
            summary = get_recipe_summary(recipe_id, recipes_dir) or ""
            snippet, end_line = get_source_snippet(
                file_path, src_line, original_source_dir,
            )
            if opcodes:
                kind = _classify_at_original_line(opcodes, src_line)
                if kind == "other":
                    kind = _classify_recipe_kind(recipe_id)
            else:
                kind = _classify_recipe_kind(recipe_id)
            rows.append(
                RecipeResolvedRow(
                    file=file_path,
                    line=src_line,
                    end_line=end_line,
                    code=snippet,
                    recipe_id=recipe_id,
                    kind=kind,
                    message=summary,
                )
            )

    rows.sort(key=lambda r: (r.file, r.line, r.recipe_id))
    return rows
