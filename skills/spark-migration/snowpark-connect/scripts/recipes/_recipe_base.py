"""Recipe base utilities shared by every LibCST recipe under ``scripts/recipes``.

Defines the dataclass + helper that each recipe uses to record one edit.
The sqlite store is optional — when ``$SCOS_FACTS_DB`` is unset, ``record_edit``
is a no-op that just returns the dataclass, so unit tests don't need a
sqlite file. The DDL below is a minimal one-table schema covering only
``recipe_edits``; the coordinator does not (yet) consume any other tables.

Public surface:

  * ``RecipeEdit``  -- dataclass for one row in the ``recipe_edits`` table.
  * ``record_edit`` -- helper recipes call once per edited line. If
                       ``facts_db`` (or ``$SCOS_FACTS_DB``) is unset this is
                       a pure no-op that just returns the dataclass, so unit
                       tests of individual recipes don't need a sqlite file.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Optional

_INIT_LOCK = threading.Lock()
_INITIALIZED_DBS: set[str] = set()

# Inline DDL for the single table this module cares about. A richer
# multi-table schema (files / symbols / imports / spark_calls / io_sites /
# secrets / recipe_edits) is possible if the coordinator ever needs it; for
# now only ``recipe_edits`` is touched, so a minimal idempotent DDL is enough.
_RECIPE_EDITS_DDL = """
CREATE TABLE IF NOT EXISTS recipe_edits (
  file                TEXT NOT NULL,
  src_line            INTEGER NOT NULL,
  recipe_id           TEXT NOT NULL,
  output_line_anchor  TEXT NOT NULL,
  PRIMARY KEY (file, src_line, recipe_id)
);
"""


def _ensure_schema(db_path: str) -> None:
    """Apply the recipe_edits DDL to ``db_path`` if not already applied.

    Idempotent: tracked per-process via ``_INITIALIZED_DBS``.
    """
    if db_path in _INITIALIZED_DBS:
        return
    with _INIT_LOCK:
        if db_path in _INITIALIZED_DBS:
            return
        with sqlite3.connect(db_path) as conn:
            conn.executescript(_RECIPE_EDITS_DDL)
            conn.commit()
        _INITIALIZED_DBS.add(db_path)


@dataclass
class RecipeEdit:
    """One row in the recipe_edits table."""

    file: str
    src_line: int
    recipe_id: str
    output_line_anchor: str


def record_edit(
    file: str,
    src_line: int,
    recipe_id: str,
    output_line_anchor: str,
    facts_db: Optional[str] = None,
) -> RecipeEdit:
    """Record that ``recipe_id`` edited ``file:src_line``.

    If ``facts_db`` is None and ``$SCOS_FACTS_DB`` is unset, this is a no-op
    that still returns the ``RecipeEdit`` dataclass so recipes are easy to
    unit-test.
    """
    if not isinstance(src_line, int) or src_line <= 0:
        raise ValueError(f"src_line must be a positive int, got {src_line!r}")
    if not file or not recipe_id or not output_line_anchor:
        raise ValueError("file, recipe_id, output_line_anchor must all be non-empty")

    edit = RecipeEdit(
        file=file,
        src_line=src_line,
        recipe_id=recipe_id,
        output_line_anchor=output_line_anchor,
    )

    db_path = facts_db or os.environ.get("SCOS_FACTS_DB")
    if not db_path:
        return edit

    _ensure_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO recipe_edits
              (file, src_line, recipe_id, output_line_anchor)
            VALUES (?, ?, ?, ?)
            """,
            (file, src_line, recipe_id, output_line_anchor),
        )
        conn.commit()
    return edit


__all__ = ["RecipeEdit", "record_edit"]
