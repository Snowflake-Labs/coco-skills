"""Tier-B unit tests for ``recipe_resolved_panel.build_recipe_resolved_panel``.

Covers:

* Translation of synthetic ``recipe_edits`` dicts to ``RecipeResolvedRow``
  lists, with kind classification by recipe-id suffix.
* Edge cases (empty / None input, malformed entries skipped, deterministic
  ordering).
* Static isolation check: the helper must not import from
  ``transform_analysis`` / ``scan_codebase`` / the IR merge logic — that
  would risk leaking recipe data into the risk/score/compatibility math
  (Recipe-Data Isolation Guarantee, Tier-B plan).
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

import recipe_resolved_panel as panel
from assess_ir import RecipeResolvedRow


@pytest.fixture(autouse=True)
def _clear_summary_cache():
    """All five lookups in the panel module use ``lru_cache`` keyed on
    file path. Clear them between tests so a synthetic fixture in one
    test doesn't leak into another."""
    for cache in (
        panel._read_recipe_summary,
        panel._read_source_lines,
        panel._parse_source_ast,
        panel._scan_scos_markers,
        panel._diff_opcodes,
    ):
        cache.cache_clear()
    yield
    for cache in (
        panel._read_recipe_summary,
        panel._read_source_lines,
        panel._parse_source_ast,
        panel._scan_scos_markers,
        panel._diff_opcodes,
    ):
        cache.cache_clear()


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_empty_inputs_yield_empty_list() -> None:
    assert panel.build_recipe_resolved_panel(None) == []
    assert panel.build_recipe_resolved_panel({}) == []
    # Falsy edit list within a present file key is also dropped.
    assert panel.build_recipe_resolved_panel({"foo.py": []}) == []


def test_classifies_by_recipe_id_suffix() -> None:
    edits = {
        "a.py": [
            {"recipe_id": "foo_rewrite", "src_line": 1},
            {"recipe_id": "bar_annotate", "src_line": 2},
            {"recipe_id": "baz_comment", "src_line": 3},
            {"recipe_id": "qux_misc", "src_line": 4},
        ]
    }
    rows = panel.build_recipe_resolved_panel(edits)
    kinds = {r.recipe_id: r.kind for r in rows}
    assert kinds == {
        "foo_rewrite": "rewrite",
        "bar_annotate": "annotate",
        "baz_comment": "comment",
        "qux_misc": "other",
    }


def test_rows_are_typed_recipe_resolved_row_instances() -> None:
    edits = {"a.py": [{"recipe_id": "x_rewrite", "src_line": 10}]}
    rows = panel.build_recipe_resolved_panel(edits)
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, RecipeResolvedRow)
    assert row.file == "a.py"
    assert row.line == 10
    assert row.recipe_id == "x_rewrite"
    assert row.kind == "rewrite"
    # Without recipes_dir, message defaults to empty (template falls back
    # to displaying recipe_id).
    assert row.message == ""


def test_deterministic_sort_by_file_then_line_then_id() -> None:
    edits = {
        "b.py": [{"recipe_id": "z_rewrite", "src_line": 5}],
        "a.py": [
            {"recipe_id": "z_rewrite", "src_line": 5},
            {"recipe_id": "a_rewrite", "src_line": 5},
            {"recipe_id": "x_rewrite", "src_line": 1},
        ],
    }
    rows = panel.build_recipe_resolved_panel(edits)
    triples = [(r.file, r.line, r.recipe_id) for r in rows]
    assert triples == [
        ("a.py", 1, "x_rewrite"),
        ("a.py", 5, "a_rewrite"),
        ("a.py", 5, "z_rewrite"),
        ("b.py", 5, "z_rewrite"),
    ]


def test_malformed_entries_are_skipped() -> None:
    """Robustness: missing / wrong-type fields are dropped silently — they
    indicate upstream contract drift, not a fatal error for the panel."""
    edits = {
        "ok.py": [{"recipe_id": "good_rewrite", "src_line": 1}],
        "bad.py": [
            {"recipe_id": "no_line"},                     # missing src_line
            {"src_line": 2},                              # missing recipe_id
            {"recipe_id": "", "src_line": 3},             # blank id
            {"recipe_id": "stringy", "src_line": "five"}, # non-int line
            "not-a-dict",                                  # wrong shape
        ],
        123: [{"recipe_id": "wrong_key_type", "src_line": 1}],  # non-str key
    }
    rows = panel.build_recipe_resolved_panel(edits)
    assert [(r.file, r.recipe_id) for r in rows] == [("ok.py", "good_rewrite")]


def test_kipawa_shaped_edits_round_trip() -> None:
    """A larger, realistic-shape input ensures groupby('file') in the Jinja
    template will produce stable file-level sections in the order the rows
    sit in. We rely on the helper to pre-sort so groupby works deterministically.
    """
    edits = {
        "main.py": [
            {"recipe_id": "spark_builder_drop_master_init_session_rewrite",
             "src_line": 12,
             "output_line_anchor": "spark_builder_drop_master_init_session_rewrite:12:abcdef12"},
            {"recipe_id": "driver_materialization_hotpath_warn_annotate",
             "src_line": 45,
             "output_line_anchor": "driver_materialization_hotpath_warn_annotate:45:fedcba98"},
        ],
        "utils.py": [
            {"recipe_id": "sparkcontext_getorcreate_rewrite", "src_line": 3},
            {"recipe_id": "sparkcontext_getorcreate_rewrite", "src_line": 18},
        ],
        "epoch_to_date.py": [
            {"recipe_id": "saveastable_format_path_rewrite", "src_line": 9},
            {"recipe_id": "implicit_spark_bootstrap_annotate", "src_line": 1},
            {"recipe_id": "rdd_to_dataframe_rewrite", "src_line": 22},
        ],
    }
    rows = panel.build_recipe_resolved_panel(edits)
    assert len(rows) == 7
    files_in_order = []
    for r in rows:
        if not files_in_order or files_in_order[-1] != r.file:
            files_in_order.append(r.file)
    # File groups should be contiguous (sorted alphabetically by file).
    assert files_in_order == ["epoch_to_date.py", "main.py", "utils.py"]
    # Within each file, rows are ascending by line.
    by_file: dict[str, list[int]] = {}
    for r in rows:
        by_file.setdefault(r.file, []).append(r.line)
    for f, lines in by_file.items():
        assert lines == sorted(lines), (
            f"lines for {f} are not sorted ascending: {lines}"
        )


# ---------------------------------------------------------------------------
# Recipe-Data Isolation Guarantee (static)
# ---------------------------------------------------------------------------


_FORBIDDEN_IMPORTS = {
    "transform_analysis",
    "scan_codebase",
}
"""Modules the panel helper MUST NOT import — those are the producers that
feed the IR merge math. The panel must remain a leaf consumer of
recipe_edits, never crossing into the analyzer or scanner data paths."""


def test_panel_helper_does_not_import_forbidden_modules() -> None:
    """Static check: parse the helper's AST and assert it imports nothing
    from the analyzer-path producers. This is the enforcement mechanism
    for the Recipe-Data Isolation Guarantee — a casual developer who adds
    a convenience import from transform_analysis will trip this test."""
    source = Path(panel.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])

    leaked = imported & _FORBIDDEN_IMPORTS
    assert not leaked, (
        f"recipe_resolved_panel must NOT import {_FORBIDDEN_IMPORTS}; "
        f"found: {leaked}. Recipe data MUST stay isolated from the "
        f"analyzer / scanner / IR-merge path (Tier-B Recipe-Data "
        f"Isolation Guarantee)."
    )


# ---------------------------------------------------------------------------
# Recipe-description extraction (dynamic, docstring-driven)
# ---------------------------------------------------------------------------


def _write_synthetic_recipe(
    recipes_dir: Path, recipe_id: str, docstring: str
) -> None:
    """Materialize a fake ``recipes/<recipe_id>/recipe.py`` with the given
    docstring as its module-level docstring."""
    d = recipes_dir / recipe_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "recipe.py").write_text(
        f'"""{docstring}"""\n\n\ndef apply():\n    pass\n',
        encoding="utf-8",
    )


def test_extract_first_paragraph_returns_one_line() -> None:
    """The first paragraph is the text up to the first blank line, with
    embedded newlines collapsed to single spaces."""
    docstring = textwrap.dedent("""\
        Rewrite ``df.checkpoint(...)`` to ``df.cache()``.

        What it does
        ------------
        Long second paragraph that should NOT appear in the summary.
    """)
    assert panel._extract_first_paragraph(docstring) == (
        "Rewrite df.checkpoint(...) to df.cache()."
    )


def test_extract_first_paragraph_handles_multi_line_first_para() -> None:
    """A paragraph that spans multiple lines is collapsed to one line."""
    docstring = textwrap.dedent("""\
        Warn on driver-side materialization (``.collect()``, ``.toPandas()``,
        ``.first()``, ``.take()``, ``.head()``) inside ``for`` / ``while``
        loops.

        Body section follows.
    """)
    out = panel._extract_first_paragraph(docstring)
    assert out is not None
    assert "Warn on driver-side materialization" in out
    assert ".collect()" in out
    assert "Body section follows" not in out
    # No embedded newlines.
    assert "\n" not in out


def test_extract_first_paragraph_strips_sphinx_roles() -> None:
    assert panel._extract_first_paragraph(
        "Calls :func:`foo` and :class:`Bar`."
    ) == "Calls foo and Bar."


def test_extract_first_paragraph_returns_none_for_empty_input() -> None:
    assert panel._extract_first_paragraph(None) is None
    assert panel._extract_first_paragraph("") is None
    assert panel._extract_first_paragraph("   \n\n   ") is None


def test_get_recipe_summary_reads_recipe_docstring(tmp_path: Path) -> None:
    """Happy path: a recipe folder with a recipe.py whose docstring opens
    with a one-paragraph summary."""
    _write_synthetic_recipe(
        tmp_path, "foo_rewrite",
        "Rewrite ``a`` to ``b``.\n\nLonger explanation.",
    )
    summary = panel.get_recipe_summary("foo_rewrite", tmp_path)
    assert summary == "Rewrite a to b."


def test_get_recipe_summary_returns_none_for_missing_recipe(tmp_path: Path) -> None:
    """Recipe folder doesn't exist → None (template falls back to id)."""
    assert panel.get_recipe_summary("ghost_rewrite", tmp_path) is None


def test_get_recipe_summary_returns_none_for_missing_docstring(tmp_path: Path) -> None:
    """Recipe file with no docstring → None."""
    d = tmp_path / "no_doc_rewrite"
    d.mkdir()
    (d / "recipe.py").write_text("def apply():\n    pass\n", encoding="utf-8")
    assert panel.get_recipe_summary("no_doc_rewrite", tmp_path) is None


def test_get_recipe_summary_returns_none_for_unparseable_file(tmp_path: Path) -> None:
    """A broken recipe.py shouldn't crash the panel — return None and let
    the template fall back."""
    d = tmp_path / "bad_rewrite"
    d.mkdir()
    (d / "recipe.py").write_text("this is not valid python {{{", encoding="utf-8")
    assert panel.get_recipe_summary("bad_rewrite", tmp_path) is None


def test_get_recipe_summary_returns_none_when_dir_omitted() -> None:
    """No recipes_dir provided → None (callers may not have one wired up,
    e.g. unit tests)."""
    assert panel.get_recipe_summary("anything_rewrite", None) is None


def test_get_recipe_summary_caches_per_recipe(tmp_path: Path) -> None:
    """The reader is lru_cache'd: editing the file after the first read
    returns the cached summary until the cache is cleared. This is an
    intentional perf optimization — recipes don't change mid-run."""
    _write_synthetic_recipe(tmp_path, "cached_rewrite", "Original summary.")
    assert panel.get_recipe_summary("cached_rewrite", tmp_path) == "Original summary."

    # Overwrite — without clearing the cache, lookup still returns original.
    _write_synthetic_recipe(tmp_path, "cached_rewrite", "New summary.")
    assert panel.get_recipe_summary("cached_rewrite", tmp_path) == "Original summary."

    panel._read_recipe_summary.cache_clear()
    assert panel.get_recipe_summary("cached_rewrite", tmp_path) == "New summary."


def test_build_panel_populates_message_when_recipes_dir_provided(tmp_path: Path) -> None:
    """End-to-end: passing recipes_dir to build_recipe_resolved_panel fills
    each row's message with the corresponding recipe's summary."""
    _write_synthetic_recipe(
        tmp_path, "alpha_rewrite",
        "Replace ``alpha`` with ``beta``.",
    )
    _write_synthetic_recipe(
        tmp_path, "gamma_annotate",
        "Annotate ``gamma`` calls with a TODO.",
    )
    edits = {
        "x.py": [
            {"recipe_id": "alpha_rewrite", "src_line": 5},
            {"recipe_id": "gamma_annotate", "src_line": 12},
            {"recipe_id": "unknown_rewrite", "src_line": 20},  # missing recipe
        ]
    }
    rows = panel.build_recipe_resolved_panel(edits, recipes_dir=tmp_path)
    by_id = {r.recipe_id: r for r in rows}
    assert by_id["alpha_rewrite"].message == "Replace alpha with beta."
    assert by_id["gamma_annotate"].message == "Annotate gamma calls with a TODO."
    # Unknown recipe → empty message (template falls back to recipe_id).
    assert by_id["unknown_rewrite"].message == ""


def test_build_panel_message_empty_when_recipes_dir_omitted() -> None:
    """Backward-compat: callers that don't wire recipes_dir get empty
    messages and the template falls back to recipe_id."""
    edits = {"x.py": [{"recipe_id": "anything_rewrite", "src_line": 1}]}
    rows = panel.build_recipe_resolved_panel(edits)
    assert rows[0].message == ""


# ---------------------------------------------------------------------------
# Source-snippet extraction (table Code column)
# ---------------------------------------------------------------------------


def test_get_source_snippet_single_line_statement(tmp_path: Path) -> None:
    """Single-line ``Assign``: AST end_lineno == lineno, so the snippet
    is one line and ``end_line == line``."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text(
        "import x\n"
        "def bar():\n"
        "    df = spark.range(10)\n"
        "    return df\n",
        encoding="utf-8",
    )
    code, end = panel.get_source_snippet("src/foo.py", 3, tmp_path)
    assert code == "df = spark.range(10)"
    assert end == 3


def test_get_source_snippet_multi_line_assignment(tmp_path: Path) -> None:
    """Multi-line ``Assign`` (parenthesized chain) — the exact case from
    Kipawa utils.py:189. AST gives us ``end_lineno`` of the closing
    paren so the user sees the full expression, not just ``n = (``."""
    (tmp_path / "a.py").write_text(
        "def f(df):\n"
        "    n = (\n"
        "        df.select(F.max(F.size('x')).alias('n'))\n"
        "        .first()\n"
        "        .n\n"
        "    )\n"
        "    return n\n",
        encoding="utf-8",
    )
    code, end = panel.get_source_snippet("a.py", 2, tmp_path)
    # Common-indent stripping: the body's 4-space prefix is removed.
    assert code == (
        "n = (\n"
        "    df.select(F.max(F.size('x')).alias('n'))\n"
        "    .first()\n"
        "    .n\n"
        ")"
    )
    assert end == 6


def test_get_source_snippet_finds_innermost_stmt(tmp_path: Path) -> None:
    """When ``src_line`` falls inside an ``if``-body, the assignment
    statement wins over the enclosing ``if`` because its span is
    tighter — that's what the user wants to see in the card."""
    (tmp_path / "a.py").write_text(
        "def f():\n"
        "    if cond:\n"
        "        x = 1\n"
        "        y = 2\n"
        "        z = 3\n",
        encoding="utf-8",
    )
    code, end = panel.get_source_snippet("a.py", 4, tmp_path)
    assert code == "y = 2"
    assert end == 4


def test_get_source_snippet_falls_back_to_single_line_when_unparseable(tmp_path: Path) -> None:
    """If the file isn't valid Python (recipe accidentally listed a YAML
    file, half-written test fixture, etc.), we still surface the line —
    just without multi-line context. ``end_line == line``."""
    (tmp_path / "a.py").write_text(
        "def f(:\n"  # invalid syntax
        "  x = 1\n",
        encoding="utf-8",
    )
    code, end = panel.get_source_snippet("a.py", 2, tmp_path)
    assert code == "x = 1"
    assert end == 2


def test_get_source_snippet_caps_long_blocks(tmp_path: Path) -> None:
    """If ``src_line`` is a ``def`` and the function body is huge, the
    snippet is truncated to ``_MAX_SNIPPET_LINES`` so a single card
    can't dominate the page. End marker tells the reader why."""
    body = "\n".join(f"    s{i} = {i}" for i in range(60))
    (tmp_path / "a.py").write_text(f"def big():\n{body}\n", encoding="utf-8")
    code, end = panel.get_source_snippet("a.py", 1, tmp_path)
    lines = code.split("\n")
    assert len(lines) == panel._MAX_SNIPPET_LINES
    assert lines[-1].startswith("# ...")
    assert "more lines" in lines[-1]
    # end_line is recomputed to match the truncated block, not the AST.
    assert end == panel._MAX_SNIPPET_LINES


def test_get_source_snippet_falls_back_to_basename(tmp_path: Path) -> None:
    """Mirror transform_analysis's path-resolution leniency."""
    (tmp_path / "deep.py").write_text("only_line = 1\n", encoding="utf-8")
    code, end = panel.get_source_snippet("nope/deep.py", 1, tmp_path)
    assert code == "only_line = 1"
    assert end == 1


def test_get_source_snippet_returns_empty_when_dir_omitted() -> None:
    assert panel.get_source_snippet("anything.py", 1, None) == ("", 0)


def test_get_source_snippet_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert panel.get_source_snippet("ghost.py", 1, tmp_path) == ("", 0)


def test_get_source_snippet_returns_empty_for_out_of_range(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    assert panel.get_source_snippet("a.py", 5, tmp_path) == ("", 0)
    assert panel.get_source_snippet("a.py", 0, tmp_path) == ("", 0)
    assert panel.get_source_snippet("a.py", -1, tmp_path) == ("", 0)


def test_get_source_snippet_returns_empty_for_blank_file_path(tmp_path: Path) -> None:
    assert panel.get_source_snippet("", 1, tmp_path) == ("", 0)


def test_build_panel_populates_code_and_end_line(tmp_path: Path) -> None:
    """End-to-end: passing ``original_source_dir`` populates BOTH
    ``code`` and ``end_line`` on each row."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text(
        "spark = SparkSession.builder.getOrCreate()\n"
        "rdd = (\n"
        "    sc.parallelize([1, 2, 3])\n"
        "    .map(lambda x: x * 2)\n"
        ")\n",
        encoding="utf-8",
    )
    edits = {
        "src/foo.py": [
            {"recipe_id": "spark_builder_drop_master_init_session_rewrite", "src_line": 1},
            {"recipe_id": "sparkcontext_property_fallback_rewrite", "src_line": 2},
        ]
    }
    rows = panel.build_recipe_resolved_panel(edits, original_source_dir=tmp_path)
    by_line = {r.line: r for r in rows}
    assert by_line[1].code == "spark = SparkSession.builder.getOrCreate()"
    assert by_line[1].end_line == 1
    assert by_line[2].code == (
        "rdd = (\n"
        "    sc.parallelize([1, 2, 3])\n"
        "    .map(lambda x: x * 2)\n"
        ")"
    )
    assert by_line[2].end_line == 5


def test_build_panel_code_empty_when_original_source_dir_omitted() -> None:
    """Backward-compat: callers that don't wire ``original_source_dir``
    get empty ``code`` / zero ``end_line`` (template hides the ``<pre>``
    and renders ``Line N`` instead of ``Line N–M``)."""
    edits = {"src/foo.py": [{"recipe_id": "foo_rewrite", "src_line": 1}]}
    rows = panel.build_recipe_resolved_panel(edits)
    assert rows[0].code == ""
    assert rows[0].end_line == 0


def test_build_panel_code_and_message_independent_enrichments(tmp_path: Path) -> None:
    """``code`` and ``message`` are populated independently: passing only
    one enrichment dir leaves the other field empty."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    edits = {"src/a.py": [{"recipe_id": "x_rewrite", "src_line": 1}]}

    rows = panel.build_recipe_resolved_panel(edits, original_source_dir=tmp_path)
    assert rows[0].code == "x = 1"
    assert rows[0].message == ""

    rows = panel.build_recipe_resolved_panel(edits, recipes_dir=tmp_path)
    assert rows[0].code == ""
    assert rows[0].end_line == 0


# ---------------------------------------------------------------------------
# SCOS marker parsing
# ---------------------------------------------------------------------------


def test_scan_scos_markers_standalone_and_trailing(tmp_path: Path) -> None:
    """Parse both standalone-line and trailing-on-code marker variants
    out of a post-recipe file."""
    f = tmp_path / "post.py"
    f.write_text(
        "import x\n"
        "    # SCOS-WARN: foo_recipe_annotate: standalone warn message\n"
        "    df.collect()\n"
        "pass  # SCOS-WARN: bar_recipe_rewrite: trailing on pass line\n"
        "    # SCOS-TODO: [SPRKCNTPY4002] baz_recipe_rewrite: todo with code prefix\n"
        "    other.code()\n",
        encoding="utf-8",
    )
    markers = panel._scan_scos_markers(f)
    assert len(markers) == 3

    m0 = markers[0]
    assert m0.line == 2 and m0.recipe_id == "foo_recipe_annotate"
    assert m0.marker_type == "WARN" and not m0.is_trailing
    assert m0.message == "standalone warn message"

    m1 = markers[1]
    assert m1.line == 4 and m1.recipe_id == "bar_recipe_rewrite"
    assert m1.marker_type == "WARN" and m1.is_trailing
    assert m1.message == "trailing on pass line"

    m2 = markers[2]
    assert m2.line == 5 and m2.recipe_id == "baz_recipe_rewrite"
    assert m2.marker_type == "TODO" and not m2.is_trailing
    assert m2.message == "todo with code prefix"


def test_scan_scos_markers_ignores_unrelated_comments(tmp_path: Path) -> None:
    """Plain comments / docstrings must not match the marker regex."""
    f = tmp_path / "post.py"
    f.write_text(
        '"""SCOS-WARN: looks like a marker but in a docstring."""\n'
        "# TODO: fix this later (no SCOS prefix)\n"
        "# SCOS but missing colon and recipe id\n"
        "x = 1\n",
        encoding="utf-8",
    )
    assert panel._scan_scos_markers(f) == ()


def test_scan_scos_markers_returns_empty_on_missing_file(tmp_path: Path) -> None:
    assert panel._scan_scos_markers(tmp_path / "ghost.py") == ()


# ---------------------------------------------------------------------------
# Diff-opcode classification
# ---------------------------------------------------------------------------


def test_classify_at_post_line_equal_means_annotate(tmp_path: Path) -> None:
    """A line preserved verbatim (only context inserted around it) maps
    to ``annotate`` — the recipe didn't modify this line itself."""
    orig = tmp_path / "orig.py"
    post = tmp_path / "post.py"
    orig.write_text("a = 1\nb = 2\n", encoding="utf-8")
    post.write_text(
        "a = 1\n"
        "# SCOS-WARN: foo: explanation\n"
        "b = 2\n",
        encoding="utf-8",
    )
    ops = panel._diff_opcodes(orig, post)
    # post line 3 = "b = 2" (equal opcode against original line 2)
    assert panel._classify_at_post_line(ops, 3) == "annotate"


def test_classify_at_post_line_insert_means_rewrite(tmp_path: Path) -> None:
    """A wholly-new line (e.g. ``pass`` replacing a chained call) maps
    to ``rewrite``."""
    orig = tmp_path / "orig.py"
    post = tmp_path / "post.py"
    orig.write_text("real_call()\n", encoding="utf-8")
    post.write_text("pass  # SCOS-WARN: r: dropped\n", encoding="utf-8")
    ops = panel._diff_opcodes(orig, post)
    assert panel._classify_at_post_line(ops, 1) == "rewrite"


def test_classify_at_original_line_replace_means_rewrite(tmp_path: Path) -> None:
    """Silent-rewrite fallback path: classify by the opcode at the
    original line. ``replace`` → ``rewrite``."""
    orig = tmp_path / "orig.py"
    post = tmp_path / "post.py"
    orig.write_text("x = df['m'][df['k']]\n", encoding="utf-8")
    post.write_text("x = element_at(df['m'], df['k'])\n", encoding="utf-8")
    ops = panel._diff_opcodes(orig, post)
    assert panel._classify_at_original_line(ops, 1) == "rewrite"


def test_classify_at_original_line_equal_means_annotate(tmp_path: Path) -> None:
    orig = tmp_path / "orig.py"
    post = tmp_path / "post.py"
    orig.write_text("a = 1\nb = 2\n", encoding="utf-8")
    post.write_text("a = 1\nb = 2\n", encoding="utf-8")
    ops = panel._diff_opcodes(orig, post)
    assert panel._classify_at_original_line(ops, 1) == "annotate"


def test_post_line_to_original_equal_block(tmp_path: Path) -> None:
    """``equal`` opcode → 1:1 positional mapping for the post→original
    rebase used by the panel."""
    orig = tmp_path / "orig.py"
    post = tmp_path / "post.py"
    orig.write_text("a\nb\nc\n", encoding="utf-8")
    post.write_text("a\nb\nc\n", encoding="utf-8")
    ops = panel._diff_opcodes(orig, post)
    assert panel._post_line_to_original(ops, 1) == 1
    assert panel._post_line_to_original(ops, 2) == 2
    assert panel._post_line_to_original(ops, 3) == 3


def test_post_line_to_original_returns_none_for_inserted_marker(tmp_path: Path) -> None:
    """When the recipe inserts a marker comment, that post line has no
    original equivalent — mapper returns ``None`` so the panel can fall
    back to post coords."""
    orig = tmp_path / "orig.py"
    post = tmp_path / "post.py"
    orig.write_text("a\nb\n", encoding="utf-8")
    post.write_text(
        "a\n"
        "# SCOS-WARN: r: inserted\n"  # post line 2 is new
        "b\n",
        encoding="utf-8",
    )
    ops = panel._diff_opcodes(orig, post)
    assert panel._post_line_to_original(ops, 1) == 1
    assert panel._post_line_to_original(ops, 2) is None  # marker insert
    assert panel._post_line_to_original(ops, 3) == 2  # b shifted by 1


def test_post_line_to_original_widened_replace_tail_is_none(tmp_path: Path) -> None:
    """Widened ``replace`` (post_span > orig_span): the prefix maps 1:1,
    the tail returns ``None`` — matches the tightened semantics in
    ``transform_analysis._build_post_to_original_line_map``."""
    orig = tmp_path / "orig.py"
    post = tmp_path / "post.py"
    orig.write_text("a\nOLD\nz\n", encoding="utf-8")
    post.write_text("a\nNEW1\nNEW2\nNEW3\nz\n", encoding="utf-8")  # 1→3 widening
    ops = panel._diff_opcodes(orig, post)
    assert panel._post_line_to_original(ops, 2) == 2  # NEW1 → OLD (1:1 prefix)
    assert panel._post_line_to_original(ops, 3) is None  # widened tail
    assert panel._post_line_to_original(ops, 4) is None  # widened tail
    assert panel._post_line_to_original(ops, 5) == 3  # z → z


def test_diff_opcodes_returns_empty_on_missing_file(tmp_path: Path) -> None:
    """Defensive: if either side fails to read, we get an empty opcodes
    tuple and the caller falls back to name-based classification."""
    orig = tmp_path / "orig.py"
    orig.write_text("x = 1\n", encoding="utf-8")
    assert panel._diff_opcodes(orig, tmp_path / "ghost.py") == ()


# ---------------------------------------------------------------------------
# Marker-driven panel — pairing, relocation, message extraction
# ---------------------------------------------------------------------------


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_marker_driven_path_relocates_line_and_uses_marker_message(
    tmp_path: Path,
) -> None:
    """The headline fix for the user-reported main.py:89 bug.

    ``recipe_edits`` claims the recipe touched original line 4 (the
    ``print`` statement); but the SCOS-TODO marker in post-recipe is
    inserted ABOVE the ``broadcast()`` call (post line 3, original
    line 2). The marker-driven path must:
      1. Use the marker's target line (post 4 → original line preserved),
      2. Show the broadcast snippet, not the print snippet,
      3. Use the marker's per-instance message, not the recipe docstring,
      4. Classify as ``annotate`` (broadcast line was preserved).
    """
    orig_dir = tmp_path / "orig"
    post_dir = tmp_path / "post"
    _write(orig_dir / "m.py", (
        "def f(self):\n"
        "    self.x = self.sparkContext.broadcast(c)\n"
        "    raise Exception('boom')\n"
        "    print('done')\n"
    ))
    _write(post_dir / "m.py", (
        "def f(self):\n"
        "    # SCOS-TODO: [SPRKCNTPY4002] foo_fallback_rewrite: cannot rewrite broadcast call -- migrate manually\n"
        "    self.x = self.sparkContext.broadcast(c)\n"
        "    raise Exception('boom')\n"
        "    print('done')\n"
    ))
    edits = {"m.py": [{"recipe_id": "foo_fallback_rewrite", "src_line": 4}]}
    rows = panel.build_recipe_resolved_panel(
        edits,
        original_source_dir=orig_dir,
        post_recipe_source_dir=post_dir,
    )
    assert len(rows) == 1
    r = rows[0]
    # The post coords are: marker at line 2, broadcast call at line 3.
    # The corresponding ORIGINAL coords are: broadcast at original line 2
    # (the marker is a recipe insert with no original equivalent). The
    # panel must display the original line for the broadcast call so it
    # aligns with the rest of the report; the snippet still includes the
    # marker comment for visual context.
    assert r.coord_system == "original"
    assert r.line == 2  # original line of the broadcast (post 3 → orig 2)
    assert r.end_line == 2
    assert "SCOS-TODO" in r.code  # marker comment included
    assert "broadcast" in r.code  # broadcast line included
    assert "print" not in r.code
    # Per-instance message comes from the marker, not the docstring.
    assert "cannot rewrite broadcast call" in r.message
    # Kind is classified at the code line (post 3) = equal → annotate,
    # even though the marker line itself is an insert.
    assert r.kind == "annotate"


def test_marker_driven_path_trailing_marker_falls_back_to_post_coords(
    tmp_path: Path,
) -> None:
    """Trailing markers (e.g. ``pass  # SCOS-WARN: ...``) land on a
    wholly-recipe-introduced line — there is no faithful original
    equivalent for the ``pass`` itself. The panel must fall back to
    POST coords for this row and flag it via ``coord_system == "post"``
    so the user knows the line number is in their post-rewrite file,
    not the original."""
    orig_dir = tmp_path / "orig"
    post_dir = tmp_path / "post"
    _write(orig_dir / "m.py", "result = builder.master('local').config(c).getOrCreate()\n")
    _write(post_dir / "m.py", (
        "result = builder.config(c).getOrCreate()\n"
        "pass  # SCOS-WARN: drop_master_rewrite: dropped .master(local) for Spark Connect\n"
    ))
    edits = {"m.py": [{"recipe_id": "drop_master_rewrite", "src_line": 1}]}
    rows = panel.build_recipe_resolved_panel(
        edits,
        original_source_dir=orig_dir,
        post_recipe_source_dir=post_dir,
    )
    assert len(rows) == 1
    r = rows[0]
    # Honest fallback: post coords + explicit flag.
    assert r.coord_system == "post"
    assert r.line == 2  # post-recipe line of the ``pass``
    assert r.code.startswith("pass")
    assert "dropped .master(local)" in r.message
    # Post line 2 is wholly new (insert opcode) → rewrite.
    assert r.kind == "rewrite"


def test_marker_driven_path_widened_replace_tail_falls_back_to_post(
    tmp_path: Path,
) -> None:
    """A widened ``replace`` opcode (post_span > orig_span) leaves the
    tail post lines with no faithful original equivalent. If a marker
    lands on a tail line, the panel must fall back to post coords for
    that row."""
    orig_dir = tmp_path / "orig"
    post_dir = tmp_path / "post"
    _write(orig_dir / "m.py", "x = old_single_line()\n")
    # The recipe replaced the single original line with three post lines.
    # The marker targets the THIRD post line (tail of the widened replace),
    # which has no original pre-image.
    _write(post_dir / "m.py", (
        "x = new_line_one()\n"
        "# SCOS-WARN: widened_rewrite: also-emitted setup\n"
        "x.helper()\n"
    ))
    edits = {"m.py": [{"recipe_id": "widened_rewrite", "src_line": 1}]}
    rows = panel.build_recipe_resolved_panel(
        edits,
        original_source_dir=orig_dir,
        post_recipe_source_dir=post_dir,
    )
    r = rows[0]
    # Target post line is 3 (line after the marker), which is in the
    # widened tail → no original equivalent → fall back to post coords.
    assert r.coord_system == "post"
    assert r.line == 3
    assert "x.helper()" in r.code


def test_silent_rewrite_path_uses_recipe_edits_with_diff_classification(
    tmp_path: Path,
) -> None:
    """Recipes that rewrite without inserting a marker (e.g.
    ``map_column_subscript_colkey_to_element_at_rewrite``) get the
    fallback: original snippet + docstring summary + diff-classified kind."""
    orig_dir = tmp_path / "orig"
    post_dir = tmp_path / "post"
    _write(orig_dir / "m.py", "x = df['m'][df['k']]\n")
    _write(post_dir / "m.py", "x = element_at(df['m'], df['k'])\n")
    edits = {"m.py": [{"recipe_id": "map_colkey_rewrite", "src_line": 1}]}
    rows = panel.build_recipe_resolved_panel(
        edits,
        original_source_dir=orig_dir,
        post_recipe_source_dir=post_dir,
    )
    assert len(rows) == 1
    r = rows[0]
    # No marker → fallback uses src_line and ORIGINAL snippet.
    assert r.line == 1
    assert r.code == "x = df['m'][df['k']]"
    # Diff opcode at original line 1 = replace → rewrite (the honest label).
    assert r.kind == "rewrite"


def test_standalone_marker_snippet_includes_comment_line(tmp_path: Path) -> None:
    """User-facing requirement: the rendered code block must show the SCOS
    comment together with the line it describes, not just the bare code.
    The displayed line range uses ORIGINAL coords (the marker line itself
    is recipe-introduced and has no original equivalent; the code line
    rebases cleanly)."""
    orig_dir = tmp_path / "orig"
    post_dir = tmp_path / "post"
    _write(orig_dir / "m.py", "def f():\n    df.collect()\n")
    _write(post_dir / "m.py", (
        "def f():\n"
        "    # SCOS-WARN: hotpath_annotate: driver materialization in a loop\n"
        "    df.collect()\n"
    ))
    edits = {"m.py": [{"recipe_id": "hotpath_annotate", "src_line": 2}]}
    rows = panel.build_recipe_resolved_panel(
        edits,
        original_source_dir=orig_dir,
        post_recipe_source_dir=post_dir,
    )
    r = rows[0]
    # Original line of df.collect() is 2 (post 3 → orig 2). The snippet
    # spans both the marker (visual context) and the code line.
    assert r.coord_system == "original"
    assert r.line == 2
    assert r.end_line == 2
    snippet_lines = r.code.splitlines()
    assert len(snippet_lines) == 2
    assert "SCOS-WARN" in snippet_lines[0]
    assert snippet_lines[1].endswith("df.collect()")


def test_marker_pairing_respects_order_for_multiple_invocations(tmp_path: Path) -> None:
    """Three edits of the same recipe in the same file pair against the
    three markers in line order. Each row gets its own per-instance
    message (the 1st, 2nd, 3rd marker's message respectively)."""
    orig_dir = tmp_path / "orig"
    post_dir = tmp_path / "post"
    _write(orig_dir / "m.py", (
        "self.x = self.sparkContext\n"
        "self.y = self.sparkContext\n"
        "self.z = self.sparkContext\n"
    ))
    _write(post_dir / "m.py", (
        "# SCOS-TODO: foo_rewrite: first invocation (x binding)\n"
        "self.x = self.sparkContext\n"
        "# SCOS-TODO: foo_rewrite: second invocation (y binding)\n"
        "self.y = self.sparkContext\n"
        "# SCOS-TODO: foo_rewrite: third invocation (z binding)\n"
        "self.z = self.sparkContext\n"
    ))
    edits = {"m.py": [
        {"recipe_id": "foo_rewrite", "src_line": 1},
        {"recipe_id": "foo_rewrite", "src_line": 2},
        {"recipe_id": "foo_rewrite", "src_line": 3},
    ]}
    rows = panel.build_recipe_resolved_panel(
        edits,
        original_source_dir=orig_dir,
        post_recipe_source_dir=post_dir,
    )
    assert len(rows) == 3
    rows_by_line = sorted(rows, key=lambda r: r.line)
    # All three rows rebase cleanly: post lines 2, 4, 6 (the assignment
    # lines after each marker) map to original lines 1, 2, 3.
    for r in rows_by_line:
        assert r.coord_system == "original"
    assert rows_by_line[0].line == 1
    assert rows_by_line[1].line == 2
    assert rows_by_line[2].line == 3
    assert "first invocation" in rows_by_line[0].message
    assert "second invocation" in rows_by_line[1].message
    assert "third invocation" in rows_by_line[2].message


def test_falls_back_when_only_original_source_dir_provided(tmp_path: Path) -> None:
    """Without ``post_recipe_source_dir`` the marker path is disabled —
    every row uses the silent-rewrite fallback."""
    orig_dir = tmp_path / "orig"
    _write(orig_dir / "m.py", "x = 1\n")
    edits = {"m.py": [{"recipe_id": "x_rewrite", "src_line": 1}]}
    rows = panel.build_recipe_resolved_panel(
        edits, original_source_dir=orig_dir,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.line == 1
    assert r.code == "x = 1"
    # No diff opcodes available → falls back to name-based suffix classification.
    assert r.kind == "rewrite"


# ---------------------------------------------------------------------------
# Scalability sanity check
# ---------------------------------------------------------------------------


def test_all_real_recipes_have_extractable_summaries() -> None:
    """Scalability sanity check: every recipe folder currently in the
    repo has a docstring whose first paragraph is extractable. This is the
    test that catches a future recipe author who forgets to add the
    docstring — the panel would silently fall back to recipe-id-only for
    that recipe."""
    recipes_dir = (
        Path(panel.__file__).parent.parent / "recipes"
    ).resolve()
    if not recipes_dir.is_dir():
        pytest.skip(f"recipes/ folder not found at {recipes_dir}")
    recipe_ids = [
        d.name for d in recipes_dir.iterdir()
        if d.is_dir()
        and (d / "recipe.py").is_file()
        and not d.name.startswith(("_", "."))
    ]
    assert recipe_ids, "expected at least one recipe folder to exist"
    missing: list[str] = []
    for rid in recipe_ids:
        summary = panel.get_recipe_summary(rid, recipes_dir)
        if not summary:
            missing.append(rid)
    assert not missing, (
        f"the following recipes have no extractable docstring summary: "
        f"{missing}. Add a one-paragraph module docstring opening with a "
        f"sentence describing what the recipe does."
    )
