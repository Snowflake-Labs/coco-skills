# flake8: noqa
"""Public SQL rewrite API — a thin wrapper over the catalog-driven engine.

``rewrite_sql`` parses once (via :mod:`rag.sql_engine`), applies the transforms
the catalog (``data/sql_rules.json``) declares, and computes the residual
findings by re-running the detectors over the result. Contract is unchanged from
the original module:

* **Parse failure ⇒ verbatim no-op** (``parsed=False``).
* **Idempotent** (re-running on rewritten output yields ``changed=False``).
* **Statement-level regeneration** — only changed statements are regenerated;
  untouched ones are copied verbatim (handled inside the engine).
* **Residual is empirical** — whatever the detectors still report after the
  rewrite (judgment-heavy gaps + any mechanical gap not auto-rewritten).

The transform bodies live in :mod:`rag.sql_rewrite_transforms`; the matcher
bodies in :mod:`rag.sql_matchers`. ``SQL_FIXER_ACTIONS`` is sourced from the
catalog (single source of truth) and re-exported for ``analyze_pyspark``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rag.sql_ast import AstFinding, analyze_sql
from rag.sql_catalog import SQL_FIXER_ACTIONS  # noqa: F401  (re-export)
from rag.sql_rewrite_transforms import SqlEdit  # noqa: F401  (re-export)


@dataclass
class SqlRewriteResult:
    new_text: str
    applied: list = field(default_factory=list)        # list[SqlEdit]
    residual: list = field(default_factory=list)        # list[AstFinding]
    parsed: bool = True
    changed: bool = False


def rewrite_sql(text: str, *, dialect: str = "spark", base_line: int = 1) -> SqlRewriteResult:
    """Rewrite mechanically-fixable SCOS SQL gaps in ``text`` (catalog-driven)."""
    from rag.sql_engine import rewrite as _rewrite  # lazy: engine imports sql_ast

    new_text, applied, parsed = _rewrite(text, dialect=dialect, base_line=base_line)
    if not parsed:
        return SqlRewriteResult(new_text=text, parsed=False)
    changed = bool(applied) and new_text != text
    residual: list[AstFinding] = []
    res = analyze_sql(new_text, base_line)
    if res is not None:
        residual = list(res.findings)
    return SqlRewriteResult(
        new_text=new_text if changed else text,
        applied=applied,
        residual=residual,
        parsed=True,
        changed=changed,
    )
