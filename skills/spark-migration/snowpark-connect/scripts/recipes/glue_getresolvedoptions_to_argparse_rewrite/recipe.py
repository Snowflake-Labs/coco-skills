"""Convert AWS Glue ``getResolvedOptions(sys.argv, [...])`` into an equivalent
``argparse`` block that still yields a **dict keyed by the bare option name**,
so every existing ``args["JOB_NAME"]`` lookup keeps working unchanged.

What it does
------------

``getResolvedOptions`` is the Glue helper that parses a job's ``--KEY value``
runtime parameters into a dict. It lives in ``awsglue.utils`` and has no SCOS
equivalent, so the args half of recipe G1 in
``references/python/glue-recipes.md`` replaces it with stdlib ``argparse``::

    args = getResolvedOptions(sys.argv, ["JOB_NAME", "INPUT_DATABASE"])

becomes::

    _parser = argparse.ArgumentParser()
    for _key in ("JOB_NAME", "INPUT_DATABASE"):
        _parser.add_argument(f"--{_key}")
    args = vars(_parser.parse_known_args()[0])

Three details in that output are load-bearing and are the whole reason this is
a recipe rather than a hand edit:

* **``vars(...)``, not the argparse namespace.** ``getResolvedOptions`` returns
  a dict, and downstream Glue code reads ``args["JOB_NAME"]``. Handing back a
  ``Namespace`` would force every lookup in the file to change to
  ``args.JOB_NAME``. ``vars()`` keeps the dict contract, so the rest of the job
  is untouched.
* **``parse_known_args()``, never ``parse_args()``.** Glue passes extra runtime
  parameters beyond the declared option list; ``parse_args()`` treats those as
  unrecognized and calls ``sys.exit(2)``, aborting the job. ``parse_known_args()``
  returns ``(namespace, unknown)`` and ignores the extras, matching
  ``getResolvedOptions``' tolerance. Index ``[0]`` takes the namespace.
* **The original LHS name is preserved.** It is not always ``args``, and the
  assignment target node is reused verbatim, so ``opts = ...`` or
  ``self.args = ...`` survive intact.

``import argparse`` is injected once at the top of the processed unit (after the
existing import block) when — and only when — a rewrite actually happened.

Trigger
-------

An assignment whose value is a call to ``getResolvedOptions`` (bare ``Name`` or
the final attribute of a dotted path such as ``awsglue.utils.getResolvedOptions``)
where the options argument — the **second positional** argument, or the keyword
argument ``options=`` — is a **literal list or tuple whose every element is a
string literal**. The literal nodes are reused verbatim in the emitted tuple, so
the original quoting style is preserved.

Helper variable names default to ``_parser`` and ``_key``. If either already
appears anywhere in the source (word-boundary match) the recipe retries with the
suffixed spellings ``_parser_scos`` / ``_key_scos``, then ``_parser_scos_2`` /
``_key_scos_2`` and so on. If no free spelling is found within a small number of
attempts the statement falls back to the TODO annotation rather than shadowing a
live binding.

Negative cases (must NOT trigger)
---------------------------------

* No ``getResolvedOptions`` token in the source (cheap substring gate).
* The options argument is a **variable**, a call, a comprehension, an
  f-string-bearing list, a starred expression, or a list containing any
  non-string-literal element — the key names are not statically knowable.
* The call has no recoverable options argument at all.
* The assignment has more than one target (``a = b = getResolvedOptions(...)``).
* No free spelling for the ``_parser`` / ``_key`` helper names.

In each of those cases the code is left **completely unchanged** and a
``# SCOS: TODO - [SPRKCNTPY3601-Fixed] …`` comment is prepended spelling out the
required argparse shape, including the ``vars()`` and ``parse_known_args()``
requirements. Guessing option names would produce a job that fails at the first
``args[...]`` lookup, which is far worse than a TODO.

Idempotency
-----------

Re-running on the recipe's own output is a byte-for-byte no-op with zero edits:
a rewritten statement no longer contains ``getResolvedOptions`` (so nothing
matches, and for a file with no other occurrence the substring gate
short-circuits), an annotated-but-unchanged statement is skipped by the leading
comment ``RECIPE_ID`` check, and the import injection is guarded by an existing
``import argparse`` check.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _annotate  # noqa: E402
import _common  # noqa: E402
import libcst as cst  # noqa: E402

RECIPE_ID = "glue_getresolvedoptions_to_argparse_rewrite"
MIN_SCOS_VERSION = "0.4.0"
NOTEBOOK_SCOPE = "cell"

_EWI = "SPRKCNTPY3601"
_TARGET_FUNC = "getResolvedOptions"

_ARGPARSE_IMPORT_TEXT = "import argparse"
_IMPORT_NODE = cst.SimpleStatementLine(
    body=[cst.Import(names=[cst.ImportAlias(name=cst.Name("argparse"))])]
)

_DEFAULT_PARSER_VAR = "_parser"
_DEFAULT_KEY_VAR = "_key"
_MAX_RENAME_ATTEMPTS = 8

_FIXED_COMMENT = (
    f"# SCOS: [{_EWI}-Fixed] {RECIPE_ID}: getResolvedOptions(sys.argv, [...]) -> "
    f"argparse; vars() keeps the result a dict keyed by the bare name so "
    f"args[\"KEY\"] lookups still work, and parse_known_args() tolerates the extra "
    f"runtime parameters that would make parse_args() abort the job"
)

_TODO_COMMENT_LINES = (
    f"# SCOS: TODO - [{_EWI}-Fixed] {RECIPE_ID}: the getResolvedOptions() option "
    f"list is not a statically-readable literal list of string literals, so the "
    f"argparse block could not be generated automatically. Convert it by hand.",
    f"# SCOS: TODO - [{_EWI}-Fixed] {RECIPE_ID}: target shape — "
    f"_parser = argparse.ArgumentParser(); for _key in (<keys>): "
    f"_parser.add_argument(f\"--{{_key}}\"); <lhs> = vars(_parser.parse_known_args()[0])",
    f"# SCOS: TODO - [{_EWI}-Fixed] {RECIPE_ID}: requirements: (1) the result MUST "
    f"stay a dict keyed by the bare name — use vars(), NOT the argparse namespace, "
    f"or every downstream args[\"KEY\"] lookup breaks; (2) use parse_known_args(), "
    f"NEVER parse_args() — Glue passes extra runtime parameters that would make "
    f"parse_args() sys.exit(2) and abort the job; (3) keep the original LHS name.",
)


# ---------------------------------------------------------------------------
# Shape predicates / extraction
# ---------------------------------------------------------------------------


def _tail_name(expr: cst.BaseExpression) -> Optional[str]:
    """Final identifier of a bare ``Name`` or a dotted ``a.b.c`` path."""
    if isinstance(expr, cst.Name):
        return expr.value
    if isinstance(expr, cst.Attribute) and isinstance(expr.attr, cst.Name):
        return expr.attr.value
    return None


def _is_get_resolved_options_call(node: cst.BaseExpression) -> bool:
    return isinstance(node, cst.Call) and _tail_name(node.func) == _TARGET_FUNC


def _options_argument(call: cst.Call) -> Optional[cst.BaseExpression]:
    """The Glue option-name list: second positional arg, or ``options=``."""
    positional = [a for a in call.args if a.keyword is None and a.star == ""]
    if len(positional) >= 2:
        return positional[1].value
    for arg in call.args:
        if arg.keyword is not None and arg.keyword.value == "options":
            return arg.value
    return None


def _literal_string_elements(
    node: cst.BaseExpression,
) -> Optional[list[cst.BaseExpression]]:
    """Return the element nodes of a literal list/tuple of string literals.

    Returns None for anything not statically readable — a variable, a
    comprehension, a starred element, an f-string, or a mixed list. The
    original literal nodes are handed back so the emitted tuple preserves the
    source quoting style.
    """
    if not isinstance(node, (cst.List, cst.Tuple)):
        return None
    out: list[cst.BaseExpression] = []
    for element in node.elements:
        # StarredElement (``*extra``) is not a plain Element -> reject.
        if not isinstance(element, cst.Element):
            return None
        if _annotate._string_value(element.value) is None:
            return None
        out.append(element.value)
    if not out:
        return None
    return out


def _name_is_free(source: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", source) is None


def _pick_helper_names(source: str) -> Optional[tuple[str, str]]:
    """Pick non-colliding ``(parser_var, key_var)`` spellings, or None.

    ``_parser`` / ``_key`` first, then ``_parser_scos`` / ``_key_scos``, then
    ``_parser_scos_2`` / ``_key_scos_2`` … Falling back to None (and therefore
    to a TODO) is preferable to shadowing a live binding.
    """
    candidates = [(_DEFAULT_PARSER_VAR, _DEFAULT_KEY_VAR), ("_parser_scos", "_key_scos")]
    candidates += [
        (f"_parser_scos_{i}", f"_key_scos_{i}") for i in range(2, _MAX_RENAME_ATTEMPTS)
    ]
    for parser_var, key_var in candidates:
        if _name_is_free(source, parser_var) and _name_is_free(source, key_var):
            return parser_var, key_var
    return None


# ---------------------------------------------------------------------------
# Replacement builders
# ---------------------------------------------------------------------------


def _keys_tuple(elements: list[cst.BaseExpression]) -> cst.Tuple:
    """``("A", "B")`` — a single-element tuple gets the trailing comma it needs
    (``("A")`` is a string, and ``for _key in "A"`` would iterate characters)."""
    comma = cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
    out: list[cst.Element] = []
    for i, value in enumerate(elements):
        is_last = i == len(elements) - 1
        if is_last:
            out.append(
                cst.Element(value=value, comma=cst.Comma())
                if len(elements) == 1
                else cst.Element(value=value)
            )
        else:
            out.append(cst.Element(value=value, comma=comma))
    return cst.Tuple(elements=out)


def _build_replacement(
    original_line: cst.SimpleStatementLine,
    assign: cst.Assign,
    elements: list[cst.BaseExpression],
    parser_var: str,
    key_var: str,
) -> list[cst.BaseStatement]:
    """The three-statement argparse block, in emission order."""
    # _parser = argparse.ArgumentParser()
    parser_stmt = cst.SimpleStatementLine(
        body=[
            cst.Assign(
                targets=[cst.AssignTarget(target=cst.Name(parser_var))],
                value=cst.Call(
                    func=cst.Attribute(
                        value=cst.Name("argparse"),
                        attr=cst.Name("ArgumentParser"),
                    ),
                    args=[],
                ),
            )
        ],
        # Carry over the original blank lines/comments, then our own marker.
        leading_lines=tuple(
            list(original_line.leading_lines)
            + [cst.EmptyLine(comment=cst.Comment(_FIXED_COMMENT))]
        ),
    )

    # for _key in (...): _parser.add_argument(f"--{_key}")
    add_argument = cst.SimpleStatementLine(
        body=[
            cst.Expr(
                value=cst.Call(
                    func=cst.Attribute(
                        value=cst.Name(parser_var),
                        attr=cst.Name("add_argument"),
                    ),
                    args=[
                        cst.Arg(
                            value=cst.FormattedString(
                                parts=[
                                    cst.FormattedStringText(value="--"),
                                    cst.FormattedStringExpression(
                                        expression=cst.Name(key_var)
                                    ),
                                ],
                                start='f"',
                                end='"',
                            )
                        )
                    ],
                )
            )
        ]
    )
    for_stmt = cst.For(
        target=cst.Name(key_var),
        iter=_keys_tuple(elements),
        body=cst.IndentedBlock(body=[add_argument]),
    )

    # <lhs> = vars(_parser.parse_known_args()[0])
    result_stmt = cst.SimpleStatementLine(
        body=[
            assign.with_changes(
                value=cst.Call(
                    func=cst.Name("vars"),
                    args=[
                        cst.Arg(
                            value=cst.Subscript(
                                value=cst.Call(
                                    func=cst.Attribute(
                                        value=cst.Name(parser_var),
                                        attr=cst.Name("parse_known_args"),
                                    ),
                                    args=[],
                                ),
                                slice=[
                                    cst.SubscriptElement(
                                        slice=cst.Index(value=cst.Integer("0"))
                                    )
                                ],
                            )
                        )
                    ],
                )
            )
        ],
        trailing_whitespace=original_line.trailing_whitespace,
    )
    return [parser_stmt, for_stmt, result_stmt]


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------


class _Recipe(_common.BaseRecipe):
    RECIPE_ID = RECIPE_ID

    def __init__(self, *, helper_names: Optional[tuple[str, str]], **kw) -> None:
        super().__init__(**kw)
        self._helper_names = helper_names
        self.rewrites_made = 0

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        start = self._line_of(original_node)
        if _annotate.comment_above_contains(self._lines, start, RECIPE_ID):
            return updated_node

        if len(updated_node.body) != 1:
            return updated_node
        small = updated_node.body[0]
        if not isinstance(small, cst.Assign):
            return updated_node
        if not _is_get_resolved_options_call(small.value):
            return updated_node

        # Only a single assignment target can be reproduced faithfully.
        if len(small.targets) != 1:
            return self._annotate_todo(updated_node, start)

        assert isinstance(small.value, cst.Call)
        options = _options_argument(small.value)
        if options is None:
            return self._annotate_todo(updated_node, start)
        elements = _literal_string_elements(options)
        if elements is None:
            return self._annotate_todo(updated_node, start)
        if self._helper_names is None:
            return self._annotate_todo(updated_node, start)

        parser_var, key_var = self._helper_names
        self.rewrites_made += 1
        self._record(
            start, "getResolvedOptions(...) -> argparse block returning a dict"
        )
        return cst.FlattenSentinel(
            _build_replacement(updated_node, small, elements, parser_var, key_var)
        )

    def _annotate_todo(self, stmt: cst.SimpleStatementLine, start: int):
        new_stmt = stmt
        for text in _TODO_COMMENT_LINES:
            new_stmt = _annotate.prepend_comment(new_stmt, text)
        self._record(
            start, "annotated non-literal getResolvedOptions() options (needs manual fix)"
        )
        return new_stmt


# ---------------------------------------------------------------------------
# Import injection
# ---------------------------------------------------------------------------


def _has_argparse_import(module: cst.Module) -> bool:
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for s in stmt.body:
            if isinstance(s, cst.Import):
                for n in s.names:
                    if (
                        isinstance(n, cst.ImportAlias)
                        and isinstance(n.name, cst.Name)
                        and n.name.value == "argparse"
                        and n.asname is None
                    ):
                        return True
    return False


def _ensure_import(module: cst.Module) -> cst.Module:
    if _has_argparse_import(module):
        return module
    body = list(module.body)
    insert_at = 0
    for i, stmt in enumerate(body):
        if isinstance(stmt, cst.SimpleStatementLine) and any(
            isinstance(s, (cst.Import, cst.ImportFrom)) for s in stmt.body
        ):
            insert_at = i + 1
        elif insert_at > 0:
            break
    new_body = body[:insert_at] + [_IMPORT_NODE] + body[insert_at:]
    return module.with_changes(body=tuple(new_body))


def apply(
    source: str, *, file: str = "<input.py>", facts_db: str | None = None
) -> _common.RecipeResult:
    if _TARGET_FUNC not in source:
        return _common.RecipeResult(source=source, edits=[])

    module = cst.parse_module(source)
    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    recipe = _Recipe(
        source=source,
        file=file,
        facts_db=facts_db,
        helper_names=_pick_helper_names(source),
    )
    new_module = wrapper.visit(recipe)
    if recipe.rewrites_made > 0:
        new_module = _ensure_import(new_module)
    return _common.RecipeResult(source=new_module.code, edits=list(recipe.edits))
