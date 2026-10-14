"""
Companion object constant scanner for Scala files.

Builds a cross-file symbol table mapping ``ObjectName.CONSTANT`` → ``"string_value"``
by pre-scanning all companion/singleton objects before the main ASG parse.

This enables resolving patterns like:

    // Schema.scala
    object EconomicPosition {
      val AM_ECONOMIC_POSITION: String = "AM_ECONOMIC_POSITION"
    }

    // BusinessRules.scala
    df.withColumn(EconomicPosition.AM_ECONOMIC_POSITION, ...)
    //            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    //            resolved via the companion symbol table
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_scala
from tree_sitter import Language, Node, Parser

SCALA_LANGUAGE = Language(tree_sitter_scala.language())


def scan_companion_objects(
    scala_files: list[Path],
) -> dict[str, str]:
    """Scan a list of Scala files and return a companion object symbol table.

    Returns a dict mapping ``"ObjectName.CONSTANT_NAME"`` to the resolved
    string value.  Only ``val`` definitions whose declared type is ``String``
    (or that carry a plain string-literal RHS without an explicit type) are
    included, since those are the column-name constants that matter for ASG
    resolution.

    Args:
        scala_files: Absolute paths to ``.scala`` files to scan.

    Returns:
        Symbol table, e.g.::

            {
                "EconomicPosition.AM_ECONOMIC_POSITION": "AM_ECONOMIC_POSITION",
                "LinkPrestations.ID_PRESTATION": "ID_PRESTATION",
            }
    """
    parser = Parser(SCALA_LANGUAGE)
    symbols: dict[str, str] = {}

    for path in scala_files:
        try:
            source = path.read_bytes()
        except OSError:
            continue
        tree = parser.parse(source)
        _extract_from_tree(tree.root_node, source, symbols)

    return symbols


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _extract_from_tree(root: Node, source: bytes, symbols: dict[str, str]) -> None:
    """Walk the CST and collect `object XYZ { val CONST: String = "value" }` entries."""
    for node in root.children:
        _visit_node(node, source, symbols)


def _visit_node(node: Node, source: bytes, symbols: dict[str, str]) -> None:
    """Recursively visit nodes, extracting companion object constants."""
    if node.type in ("object_definition", "class_definition"):
        obj_name = _get_object_name(node, source)
        if obj_name:
            _extract_object_vals(node, source, obj_name, symbols)
        return

    for child in node.children:
        _visit_node(child, source, symbols)


def _get_object_name(node: Node, source: bytes) -> str | None:
    """Extract the name identifier from an object/class definition node."""
    for child in node.named_children:
        if child.type == "identifier":
            return _text(child, source)
    return None


def _extract_object_vals(
    obj_node: Node,
    source: bytes,
    obj_name: str,
    symbols: dict[str, str],
) -> None:
    """Walk the body of an object/class and collect String val constants."""
    # Find the template/body block
    for child in obj_node.named_children:
        if child.type in ("template_body", "block"):
            _scan_val_definitions(child, source, obj_name, symbols)
            return
    # Fallback: scan all named children
    _scan_val_definitions(obj_node, source, obj_name, symbols)


def _scan_val_definitions(
    body_node: Node,
    source: bytes,
    obj_name: str,
    symbols: dict[str, str],
) -> None:
    """Collect val/var definitions that are plain string literals."""
    for child in body_node.named_children:
        # Recurse into nested objects/classes
        if child.type in ("object_definition", "class_definition"):
            nested_name = _get_object_name(child, source)
            if nested_name:
                _extract_object_vals(child, source, nested_name, symbols)
            continue

        if child.type not in ("val_definition", "var_definition"):
            continue

        val_name = None
        val_type: str | None = None
        val_str: str | None = None

        named = child.named_children
        # First identifier is the name
        for c in named:
            if c.type == "identifier":
                val_name = _text(c, source)
                break

        if not val_name:
            continue

        # Look for type annotation and RHS value
        for c in named:
            t = c.type
            if t == "type_identifier":
                val_type = _text(c, source)
            elif t in ("string", "interpolated_string_expression"):
                val_str = _extract_string_value(c, source)

        # Only include if the type is String (or inferred from a plain string RHS)
        if val_str is not None and (val_type in (None, "String") or val_type is None):
            key = f"{obj_name}.{val_name}"
            symbols[key] = val_str


def _extract_string_value(node: Node, source: bytes) -> str | None:
    """Extract the raw string content from a string/interpolated_string node."""
    raw = _text(node, source)
    # Strip surrounding quotes: "..." or """..."""
    for quote in ('"""', '"', "'"):
        if raw.startswith(quote) and raw.endswith(quote) and len(raw) > 2 * len(quote) - 1:
            return raw[len(quote):-len(quote)]
    # Interpolated: s"..." → strip s" prefix and " suffix
    if len(raw) >= 3 and raw[1] == '"':
        return raw[2:-1]
    return None
